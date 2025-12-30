from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib.admin.views.decorators import staff_member_required
from django.views.decorators.cache import never_cache
from django.contrib import messages
from django.http import HttpResponse
from .models import Project, Pole, StageDefinition, Evidence, ItemFieldValue, Client
from .forms import EvidenceForm, DynamicItemForm
from .utils import watermark_image, get_gps_from_image
from .models import ProjectIssue
from .forms import IssueForm

# ==========================================
# 1. MAIN DASHBOARD
# ==========================================
@never_cache
@login_required
def dashboard(request):
    is_admin = request.user.is_superuser or request.user.is_staff
    
    if is_admin:
        projects_query = Project.objects.all().order_by('-created_at')
    else:
        projects_query = Project.objects.filter(contractors=request.user).order_by('-created_at')
    
    active_projects = projects_query.filter(status='ACTIVE')
    completed_projects = projects_query.filter(status='COMPLETED')

    return render(request, 'tracker/dashboard.html', {
        'active_projects': active_projects,
        'completed_projects': completed_projects,
        'is_admin': is_admin
    })

# ==========================================
# 2. PROJECT MANAGEMENT
# ==========================================
@login_required
def project_detail(request, project_id):
    project = get_object_or_404(Project, id=project_id)
    poles = project.poles.all()
    return render(request, 'tracker/project_detail.html', {'project': project, 'poles': poles})

@login_required
def mark_project_completed(request, project_id):
    if not request.user.is_superuser:
        return HttpResponse("Unauthorized", status=401)
    project = get_object_or_404(Project, id=project_id)
    project.status = 'COMPLETED'
    project.save()
    return redirect('dashboard')

@login_required
def create_project_item(request, project_id):
    project = get_object_or_404(Project, id=project_id)
    
    if request.method == 'POST':
        form = DynamicItemForm(project, request.POST)
        if form.is_valid():
            # A. Create with TEMP ID
            temp_id = f"TEMP-{project.poles.count() + 1}"
            pole = Pole.objects.create(project=project, identifier=temp_id)
            
            # B. Save Answers
            for field_def in project.field_definitions.all():
                field_name = f"custom_{field_def.id}"
                answer = form.cleaned_data.get(field_name)
                ItemFieldValue.objects.create(pole=pole, field_def=field_def, value=answer)
            
            # C. HIERARCHICAL NAMING LOGIC: "City_Village #1"
            new_identifier = ""
            group_def = project.field_definitions.filter(is_grouping_key=True).first()
            
            if group_def:
                val_obj = ItemFieldValue.objects.filter(pole=pole, field_def=group_def).first()
                group_value = val_obj.value if val_obj else ""
                
                if group_value:
                    count = ItemFieldValue.objects.filter(
                        field_def=group_def, value=group_value, pole__project=project
                    ).count()
                    # FORMAT: "Ayodhya_Bari #1"
                    new_identifier = f"{project.name}_{group_value} #{count}"
            
            # Fallback Naming
            if not new_identifier:
                new_identifier = f"{project.project_type.unit_name} #{project.poles.count()}"

            # Ensure Unique
            base = new_identifier
            c = 1
            while project.poles.filter(identifier=new_identifier).exclude(id=pole.id).exists():
                new_identifier = f"{base}-{c}"
                c += 1

            pole.identifier = new_identifier
            pole.save()
            
            messages.success(request, f"Created {new_identifier}!")
            return redirect('project_detail', project_id=project.id)
    else:
        form = DynamicItemForm(project)
    return render(request, 'tracker/add_item.html', {'project': project, 'form': form})

# ==========================================
# 3. POLE / EVIDENCE HANDLING
# ==========================================
@login_required
def pole_detail(request, pole_id):
    pole = get_object_or_404(Pole, id=pole_id)
    stages = pole.project.project_type.stages.all().order_by('order')
    existing_evidence = Evidence.objects.filter(pole=pole)
    evidence_map = {e.stage.id: e for e in existing_evidence}

    if request.method == 'POST':
        stage_id = request.POST.get('stage_id')
        lat = request.POST.get('gps_lat')
        lon = request.POST.get('gps_long')
        raw_file = request.FILES.get('image')

        if stage_id:
            stage_obj = get_object_or_404(StageDefinition, id=stage_id)
            Evidence.objects.filter(pole=pole, stage=stage_obj).delete()

        form = EvidenceForm(request.POST, request.FILES)

        if form.is_valid():
            try:
                evidence = form.save(commit=False)
                evidence.pole = pole
                if stage_id:
                    evidence.stage = stage_obj

                # GPS Logic
                if (not lat or not lon) and raw_file:
                    try:
                        if hasattr(raw_file, 'seek'): raw_file.seek(0)
                        exif_lat, exif_lon = get_gps_from_image(raw_file)
                        if exif_lat and exif_lon:
                            lat, lon = exif_lat, exif_lon
                            evidence.gps_lat, evidence.gps_long = lat, lon
                    except Exception:
                        pass 

                evidence.save() 

                # Watermark Logic
                if lat and lon and raw_file:
                    try:
                        if hasattr(raw_file, 'seek'): raw_file.seek(0)
                        branded_photo = watermark_image(raw_file, lat, lon)
                        branded_photo.name = raw_file.name 
                        evidence.image = branded_photo 
                        evidence.save()
                    except Exception as e:
                        print(f"Watermark Failed: {e}")

                # Auto-Complete Logic
                required_count = StageDefinition.objects.filter(project_type=pole.project.project_type, is_required=True).count()
                uploaded_count = Evidence.objects.filter(pole=pole, stage__is_required=True).values('stage').distinct().count()

                pole.is_completed = (uploaded_count >= required_count)
                pole.save()

                messages.success(request, "Upload successful!")
                return redirect('pole_detail', pole_id=pole.id)
            except Exception as e:
                print(f"Save Error: {e}")
                messages.error(request, f"Error saving photo: {e}")
        else:
            messages.error(request, "Upload failed.")
    else:
        form = EvidenceForm()

    return render(request, 'tracker/pole_detail.html', {'pole': pole, 'stages': stages, 'evidence_map': evidence_map, 'form': form})

@login_required
def delete_evidence(request, evidence_id):
    evidence = get_object_or_404(Evidence, id=evidence_id)
    pole = evidence.pole
    if request.user.is_authenticated:
        evidence.delete()
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
# 4. CLIENT VIEWS (HIERARCHY & MAGIC LINKS)
# ==========================================

def client_dashboard(request, client_uuid):
    """
    LEVEL 1: Shows all Cities (Projects) for this Client Organization
    """
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
            
            # CHECK FOR SPECIFIC ISSUE ON THIS POLE
            has_issue = pole.issues.filter(status='OPEN').exists()

            grouped_data[village_name]['poles'].append({
                'pole': pole,
                'history': history,
                'has_issue': has_issue # <--- Sending this to template
            })
            
            grouped_data[village_name]['total'] += 1
            if pole.is_completed:
                grouped_data[village_name]['done'] += 1
        
        for v_name, data in grouped_data.items():
            data['percent'] = int((data['done'] / data['total']) * 100) if data['total'] > 0 else 0
            
        grouped_data = dict(sorted(grouped_data.items()))
    else:
        # Fallback logic
        grouped_data['All Locations'] = {'poles': [], 'done': done, 'total': total, 'percent': progress}
        for pole in poles:
            history = pole.evidence.all().order_by('stage__order')
            has_issue = pole.issues.filter(status='OPEN').exists()
            grouped_data['All Locations']['poles'].append({
                'pole': pole, 
                'history': history,
                'has_issue': has_issue
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
            messages.success(request, "Issue reported to the admin.")
    
    # Redirect back to the client view (we need the client_uuid)
    return redirect('client_view', client_uuid=pole.project.client_uuid)

@login_required
def project_issues(request, project_id):
    project = get_object_or_404(Project, id=project_id)
    # Get all OPEN issues for this project
    issues = ProjectIssue.objects.filter(pole__project=project, status='OPEN').order_by('-created_at')
    
    return render(request, 'tracker/project_issues.html', {
        'project': project,
        'issues': issues
    })

@login_required
def resolve_issue(request, issue_id):
    issue = get_object_or_404(ProjectIssue, id=issue_id)
    issue.status = 'RESOLVED'
    issue.save()
    messages.success(request, "Issue marked as resolved.")
    # Send user back to the issues list
    return redirect('project_issues', project_id=issue.pole.project.id)