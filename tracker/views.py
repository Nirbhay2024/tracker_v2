import sys
import csv
from django.core.files.uploadedfile import InMemoryUploadedFile
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib.admin.views.decorators import staff_member_required
from django.views.decorators.cache import never_cache
from django.contrib import messages
from django.http import HttpResponse
from django.db.models import Q
from django.utils import timezone
from .models import Project, Pole, StageDefinition, Evidence, ItemFieldValue, Client, ProjectIssue, ProjectLog
from .forms import EvidenceForm, DynamicItemForm, IssueForm
from .utils import watermark_image, get_gps_from_image

# ==========================================
# 0. LOGGING HELPER
# ==========================================
def log_action(project, user, action, target, details="", lat=None, lon=None):
    """Helper to record an audit log entry."""
    try:
        ProjectLog.objects.create(
            project=project,
            user=user if user.is_authenticated else None,
            action=action,
            target=target,
            details=details,
            gps_lat=lat,
            gps_long=lon
        )
    except Exception as e:
        print(f"Logging Failed: {e}")

# ==========================================
# 1. MAIN DASHBOARD
# ==========================================
@never_cache
@login_required
def dashboard(request):
    is_admin = request.user.is_superuser or request.user.is_staff
    
    # Self-Healing: Fix Missing IDs
    poles_missing_ids = Pole.objects.filter(custom_id__isnull=True)
    if poles_missing_ids.exists():
        for p in poles_missing_ids:
            p.save()
    
    if is_admin:
        projects_query = Project.objects.all().order_by('-created_at')
    else:
        projects_query = Project.objects.filter(contractors=request.user).order_by('-created_at')
    
    active_projects = projects_query.filter(status='ACTIVE')
    completed_projects = projects_query.filter(status='COMPLETED')

    search_query = request.GET.get('q')
    search_results = None
    
    if search_query:
        search_results = Pole.objects.filter(
            Q(identifier__icontains=search_query) | 
            Q(custom_id__icontains=search_query) 
        )
        if not is_admin:
            search_results = search_results.filter(project__contractors=request.user)

    return render(request, 'tracker/dashboard.html', {
        'active_projects': active_projects,
        'completed_projects': completed_projects,
        'is_admin': is_admin,
        'search_query': search_query,
        'search_results': search_results
    })

# ==========================================
# 2. PROJECT MANAGEMENT & LOGS
# ==========================================
@login_required
def project_detail(request, project_id):
    project = get_object_or_404(Project, id=project_id)
    poles = sorted(project.poles.all(), key=lambda p: (not p.has_open_issue, p.id))
    return render(request, 'tracker/project_detail.html', {'project': project, 'poles': poles})

@login_required
def project_logs(request, project_id):
    project = get_object_or_404(Project, id=project_id)
    # Security: Only admins or assigned contractors can view logs
    if not (request.user.is_superuser or request.user.is_staff or project.contractors.filter(id=request.user.id).exists()):
        return HttpResponse("Unauthorized", status=401)
        
    logs = project.logs.all()
    return render(request, 'tracker/project_logs.html', {'project': project, 'logs': logs})

@login_required
def export_project_logs(request, project_id):
    project = get_object_or_404(Project, id=project_id)
    if not (request.user.is_superuser or request.user.is_staff or project.contractors.filter(id=request.user.id).exists()):
        return HttpResponse("Unauthorized", status=401)

    response = HttpResponse(content_type='text/csv')
    filename = f"Project_Logs_{project.name}_{timezone.now().strftime('%Y%m%d')}.csv"
    response['Content-Disposition'] = f'attachment; filename="{filename}"'

    writer = csv.writer(response)
    writer.writerow(['Timestamp', 'User', 'Action', 'Target', 'Details', 'Latitude', 'Longitude'])

    for log in project.logs.all():
        writer.writerow([
            log.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            log.user.username if log.user else "System/Client",
            log.action,
            log.target,
            log.details,
            log.gps_lat,
            log.gps_long
        ])

    return response

@login_required
def mark_project_completed(request, project_id):
    if not request.user.is_superuser:
        return HttpResponse("Unauthorized", status=401)
    project = get_object_or_404(Project, id=project_id)
    project.status = 'COMPLETED'
    project.save()
    
    log_action(project, request.user, "Project Completed", "Project Settings", "Marked status as COMPLETED")
    
    return redirect('dashboard')

@login_required
def create_project_item(request, project_id):
    project = get_object_or_404(Project, id=project_id)
    
    if request.method == 'POST':
        form = DynamicItemForm(project, request.POST)
        if form.is_valid():
            temp_id = f"TEMP-{project.poles.count() + 1}"
            pole = Pole.objects.create(project=project, identifier=temp_id)
            
            # Collect custom data for the log
            custom_data_log = []
            
            for field_def in project.field_definitions.all():
                field_name = f"custom_{field_def.id}"
                answer = form.cleaned_data.get(field_name)
                ItemFieldValue.objects.create(pole=pole, field_def=field_def, value=answer)
                custom_data_log.append(f"{field_def.label}: {answer}")
            
            # --- Identifier Logic ---
            new_identifier = ""
            group_def = project.field_definitions.filter(is_grouping_key=True).first()
            if group_def:
                val_obj = ItemFieldValue.objects.filter(pole=pole, field_def=group_def).first()
                group_value = val_obj.value if val_obj else ""
                if group_value:
                    count = ItemFieldValue.objects.filter(field_def=group_def, value=group_value, pole__project=project).count()
                    new_identifier = f"{project.name}_{group_value} #{count}"
            
            if not new_identifier:
                new_identifier = f"{project.project_type.unit_name} #{project.poles.count()}"

            base = new_identifier
            c = 1
            while project.poles.filter(identifier=new_identifier).exclude(id=pole.id).exists():
                new_identifier = f"{base}-{c}"
                c += 1

            pole.identifier = new_identifier
            pole.save()
            
            # --- LOGGING ---
            log_details = " | ".join(custom_data_log)
            log_action(project, request.user, "Created Item", pole.identifier, f"Custom Fields: {log_details}")
            
            messages.success(request, f"Created {new_identifier}!")
            return redirect('project_detail', project_id=project.id)
    else:
        form = DynamicItemForm(project)
    return render(request, 'tracker/add_item.html', {'project': project, 'form': form})

# ==========================================
# 3. POLE / EVIDENCE HANDLING
# ==========================================

# ... (Imports remain the same) ...

@login_required
def pole_detail(request, pole_id):
    pole = get_object_or_404(Pole, id=pole_id)
    stages = pole.project.project_type.stages.all().order_by('order')
    existing_evidence = Evidence.objects.filter(pole=pole)
    evidence_map = {e.stage.id: e for e in existing_evidence}

    # --- 1. CALCULATE LOCK STATUS ---
    # Logic: A stage is "locked" if the immediate previous stage is not done.
    # We iterate through the sorted stages to determine this.
    previous_stage_done = True # First stage is always open
    for stage in stages:
        # We attach a temporary attribute to the object for the template
        stage.is_locked = not previous_stage_done
        
        # Check if this stage is done for the NEXT iteration
        if stage.id in evidence_map:
            previous_stage_done = True
        else:
            previous_stage_done = False

    if request.method == 'POST':
        stage_id = request.POST.get('stage_id')
        lat = request.POST.get('gps_lat')
        lon = request.POST.get('gps_long')
        raw_file = request.FILES.get('image')

        # --- 2. SECURITY CHECK: PREVENT SKIPPING ---
        if stage_id:
            target_stage = get_object_or_404(StageDefinition, id=stage_id)
            
            # Find all stages that come BEFORE this one
            prev_stages = StageDefinition.objects.filter(
                project_type=pole.project.project_type,
                order__lt=target_stage.order
            )
            
            # Check if any previous stage is missing evidence
            missing_stages = []
            for ps in prev_stages:
                if not Evidence.objects.filter(pole=pole, stage=ps).exists():
                    missing_stages.append(ps.name)
            
            if missing_stages:
                messages.error(request, f"⛔ Sequence Locked! You must complete: {', '.join(missing_stages)} first.")
                return redirect('pole_detail', pole_id=pole.id)

        # (Existing Deletion Logic)
        if stage_id:
            stage_obj = get_object_or_404(StageDefinition, id=stage_id)
            if Evidence.objects.filter(pole=pole, stage=stage_obj).exists():
                Evidence.objects.filter(pole=pole, stage=stage_obj).delete()
                log_action(pole.project, request.user, "Re-Uploaded Evidence", pole.identifier, f"Overwrote stage: {stage_obj.name}")

        form = EvidenceForm(request.POST, request.FILES)

        if form.is_valid():
            try:
                evidence = form.save(commit=False)
                evidence.pole = pole
                if stage_id:
                    evidence.stage = stage_obj

                if (not lat or not lon) and raw_file:
                    try:
                        if hasattr(raw_file, 'seek'): raw_file.seek(0)
                        exif_lat, exif_lon = get_gps_from_image(raw_file)
                        if exif_lat and exif_lon:
                            lat, lon = exif_lat, exif_lon
                    except Exception as e:
                        print(f"DEBUG: EXIF Error: {e}")

                if lat and lon:
                    evidence.gps_lat = lat
                    evidence.gps_long = lon

                if raw_file:
                    try:
                        if hasattr(raw_file, 'seek'): raw_file.seek(0)
                        branded_content = watermark_image(raw_file, lat, lon)
                        branded_file = InMemoryUploadedFile(
                            file=branded_content, field_name=None, name=raw_file.name,
                            content_type='image/jpeg', size=branded_content.tell(), charset=None
                        )
                        evidence.image = branded_file
                    except Exception as e:
                        print(f"Watermark Error: {e}")

                evidence.save() 
                
                log_action(
                    pole.project, request.user, "Uploaded Evidence", pole.identifier, 
                    f"Stage: {evidence.stage.name}", lat=lat, lon=lon
                )

                required_count = StageDefinition.objects.filter(project_type=pole.project.project_type, is_required=True).count()
                uploaded_count = Evidence.objects.filter(pole=pole, stage__is_required=True).values('stage').distinct().count()
                pole.is_completed = (uploaded_count >= required_count)
                pole.save()

                messages.success(request, "Upload successful!")
                return redirect('pole_detail', pole_id=pole.id)
            except Exception as e:
                messages.error(request, f"Error: {e}")
        else:
            messages.error(request, "Upload failed.")
    else:
        form = EvidenceForm()

    return render(request, 'tracker/pole_detail.html', {'pole': pole, 'stages': stages, 'evidence_map': evidence_map, 'form': form})


@login_required
def delete_evidence(request, evidence_id):
    evidence = get_object_or_404(Evidence, id=evidence_id)
    pole = evidence.pole
    stage_name = evidence.stage.name
    
    if request.user.is_authenticated:
        evidence.delete()
        
        # --- LOGGING ---
        log_action(pole.project, request.user, "Deleted Evidence", pole.identifier, f"Deleted photo for: {stage_name}")
        
        required_count = StageDefinition.objects.filter(project_type=pole.project.project_type, is_required=True).count()
        uploaded_count = Evidence.objects.filter(pole=pole, stage__is_required=True).values('stage').distinct().count()
        pole.is_completed = (uploaded_count >= required_count)
        pole.save()
        
    return redirect('pole_detail', pole_id=pole.id)

@staff_member_required
def admin_project_inspection(request, project_id):
    project = get_object_or_404(Project, id=project_id)
    poles = project.poles.all()
    inspection_data = {}
    for pole in poles:
        photos = Evidence.objects.filter(pole=pole).order_by('stage__order')
        inspection_data[pole] = photos
    return render(request, 'tracker/admin_inspection.html', {'project': project, 'inspection_data': inspection_data})

# ==========================================
# 4. CLIENT / ISSUE VIEWS
# ==========================================
def client_dashboard(request, client_uuid):
    client_org = get_object_or_404(Client, uuid=client_uuid)
    projects = client_org.projects.all().order_by('-created_at')
    
    total_poles = 0
    completed_poles = 0
    for p in projects:
        total_poles += p.poles.count()
        completed_poles += p.poles.filter(is_completed=True).count()
    overall_progress = int((completed_poles/total_poles)*100) if total_poles > 0 else 0
    return render(request, 'tracker/client_dashboard.html', {
        'client': client_org,
        'projects': projects,
        'overall_progress': overall_progress
    })

def client_city_view(request, client_uuid):
    project = get_object_or_404(Project, client_uuid=client_uuid)
    poles = project.poles.all()
    total = poles.count()
    done = poles.filter(is_completed=True).count()
    progress = int((done/total)*100) if total > 0 else 0
    group_def = project.field_definitions.filter(is_grouping_key=True).first()
    grouped_data = {}
    
    if group_def:
        for pole in poles:
            val_obj = pole.custom_values.filter(field_def=group_def).first()
            village_name = val_obj.value if (val_obj and val_obj.value) else "General"
            if village_name not in grouped_data:
                grouped_data[village_name] = {'poles': [], 'done': 0, 'total': 0}
            history = pole.evidence.all().order_by('stage__order')
            custom_data = pole.custom_values.select_related('field_def').all()
            has_issue = pole.issues.filter(status='OPEN').exists()
            grouped_data[village_name]['poles'].append({
                'pole': pole,
                'history': history,
                'has_issue': has_issue,
                'custom_data': custom_data
            })
            grouped_data[village_name]['total'] += 1
            if pole.is_completed:
                grouped_data[village_name]['done'] += 1
        for v_name, data in grouped_data.items():
            data['percent'] = int((data['done'] / data['total']) * 100) if data['total'] > 0 else 0
        grouped_data = dict(sorted(grouped_data.items()))
    else:
        grouped_data['All Locations'] = {'poles': [], 'done': done, 'total': total, 'percent': progress}
        for pole in poles:
            history = pole.evidence.all().order_by('stage__order')
            custom_data = pole.custom_values.select_related('field_def').all()
            has_issue = pole.issues.filter(status='OPEN').exists()
            grouped_data['All Locations']['poles'].append({
                'pole': pole, 
                'history': history,
                'has_issue': has_issue,
                'custom_data': custom_data
            })
    return render(request, 'tracker/client_city_view.html', {
        'project': project,
        'grouped_data': grouped_data,
        'progress': progress
    })

def report_issue(request, pole_id):
    pole = get_object_or_404(Pole, id=pole_id)
    if request.method == 'POST':
        form = IssueForm(request.POST)
        if form.is_valid():
            ProjectIssue.objects.create(
                pole=pole,
                message=form.cleaned_data['message']
            )
            # --- LOGGING ---
            # User is None because this comes from the Client (Magic Link)
            log_action(pole.project, None, "Client Flagged Issue", pole.identifier, f"Issue: {form.cleaned_data['message']}")
            
            messages.success(request, "Issue reported to the admin.")
    return redirect('client_view', client_uuid=pole.project.client_uuid)

@login_required
def project_issues(request, project_id):
    project = get_object_or_404(Project, id=project_id)
    issues = ProjectIssue.objects.filter(pole__project=project, status='OPEN').order_by('-created_at')
    return render(request, 'tracker/project_issues.html', {'project': project, 'issues': issues})

@login_required
def resolve_issue(request, issue_id):
    issue = get_object_or_404(ProjectIssue, id=issue_id)
    issue.status = 'RESOLVED'
    issue.save()
    
    # --- LOGGING ---
    log_action(issue.pole.project, request.user, "Resolved Issue", issue.pole.identifier, f"Resolved report from {issue.reported_by}")
    
    messages.success(request, "Issue marked as resolved.")
    return redirect('project_issues', project_id=issue.pole.project.id)