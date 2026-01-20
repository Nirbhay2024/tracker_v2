import sys
import csv
import logging
from django.core.files.uploadedfile import InMemoryUploadedFile
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib.admin.views.decorators import staff_member_required
from django.views.decorators.cache import never_cache
from django.contrib import messages
from django.http import HttpResponse
from django.core.exceptions import PermissionDenied
from django.db.models import Q, Count, Case, When, IntegerField, Prefetch
from django.utils import timezone
from django.core.paginator import Paginator
from .models import Project, Pole, StageDefinition, Evidence, ItemFieldValue, Client, ProjectIssue, ProjectLog
from .forms import EvidenceForm, DynamicItemForm, IssueForm
from .utils import watermark_image, get_gps_from_image, rate_limit

# Configure standard logger
logger = logging.getLogger(__name__)

# ==========================================
# 0. SECURITY & LOGGING HELPERS
# ==========================================
def check_project_access(user, project):
    """
    SECURITY: Ensures the user has explicit permission to access this project.
    - Superusers/Staff: Access All.
    - Contractors: Must be assigned to the project.
    """
    if user.is_superuser or user.is_staff:
        return True
    
    # Check if the user is in the assigned contractors list
    if project.contractors.filter(id=user.id).exists():
        return True
        
    # Log the unauthorized attempt for security auditing
    logger.warning(f"SECURITY ALERT: User {user.username} (ID: {user.id}) tried to access Project {project.id} without permission.")
    raise PermissionDenied("You are not authorized to access this project.")

def log_action(project, user, action, target, details="", lat=None, lon=None):
    """Helper to record an audit log entry."""
    try:
        user_obj = user if (user and user.is_authenticated) else None
        ProjectLog.objects.create(
            project=project,
            user=user_obj,
            action=action,
            target=target,
            details=details,
            gps_lat=lat,
            gps_long=lon
        )
    except Exception as e:
        # Use proper logging instead of print
        logger.error(f"AUDIT LOG FAILURE: Could not save log entry. Error: {e}", exc_info=True)

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
    
    # [FIX] SECURITY: Input Sanitization
    if search_query:
        # Limit length to prevent DoS via massive regex
        search_query = search_query[:100].strip()
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
    check_project_access(request.user, project)  # <--- SECURITY CHECK
    
    poles = sorted(project.poles.all(), key=lambda p: (not p.has_open_issue, p.id))
    return render(request, 'tracker/project_detail.html', {'project': project, 'poles': poles})

@login_required
def project_logs(request, project_id):
    project = get_object_or_404(Project, id=project_id)
    check_project_access(request.user, project)  # <--- SECURITY CHECK
    
    logs = project.logs.all()
    return render(request, 'tracker/project_logs.html', {'project': project, 'logs': logs})

@login_required
def export_project_logs(request, project_id):
    project = get_object_or_404(Project, id=project_id)
    check_project_access(request.user, project)  # <--- SECURITY CHECK

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
        raise PermissionDenied("Only admins can mark projects as completed.")
        
    project = get_object_or_404(Project, id=project_id)
    project.status = 'COMPLETED'
    project.save()
    
    log_action(project, request.user, "Project Completed", "Project Settings", "Marked status as COMPLETED")
    
    return redirect('dashboard')

@login_required
def create_project_item(request, project_id):
    project = get_object_or_404(Project, id=project_id)
    check_project_access(request.user, project)  # <--- SECURITY CHECK
    
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

@login_required
def pole_detail(request, pole_id):
    pole = get_object_or_404(Pole, id=pole_id)
    check_project_access(request.user, pole.project)  # <--- SECURITY CHECK
    
    stages = pole.project.project_type.stages.all().order_by('order')
    existing_evidence = Evidence.objects.filter(pole=pole)
    evidence_map = {e.stage.id: e for e in existing_evidence}

    # --- 1. CALCULATE LOCK STATUS ---
    previous_stage_done = True 
    for stage in stages:
        stage.is_locked = not previous_stage_done
        if stage.id in evidence_map:
            previous_stage_done = True
        else:
            previous_stage_done = False

    if request.method == 'POST':
        # [FIX] SECURITY: Validate inputs immediately
        try:
            stage_id_raw = request.POST.get('stage_id')
            stage_id = int(stage_id_raw) if stage_id_raw else None
        except ValueError:
            messages.error(request, "Invalid Stage ID.")
            return redirect('pole_detail', pole_id=pole.id)
            
        lat = request.POST.get('gps_lat')
        lon = request.POST.get('gps_long')
        # Simple coordinate validation
        if lat and len(lat) > 20: lat = lat[:20]
        if lon and len(lon) > 20: lon = lon[:20]
        raw_file = request.FILES.get('image')

        # --- 2. SECURITY CHECK: PREVENT SKIPPING ---
        if stage_id:
            target_stage = get_object_or_404(StageDefinition, id=stage_id)
            
            # Find all stages that come BEFORE this one
            prev_stages = StageDefinition.objects.filter(
                project_type=pole.project.project_type,
                order__lt=target_stage.order
            )
            
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

                # EXIF Fallback
                if (not lat or not lon) and raw_file:
                    try:
                        if hasattr(raw_file, 'seek'): raw_file.seek(0)
                        exif_lat, exif_lon = get_gps_from_image(raw_file)
                        if exif_lat and exif_lon:
                            lat, lon = exif_lat, exif_lon
                    except Exception as e:
                        logger.warning(f"EXIF Extraction Failed: {e}")

                if lat and lon:
                    evidence.gps_lat = lat
                    evidence.gps_long = lon

                # Watermarking
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
                        logger.error(f"Watermarking Failed: {e}")

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
                logger.error(f"Upload Error: {e}")
                messages.error(request, f"Error: {e}")
        else:
            messages.error(request, "Upload failed.")
    else:
        form = EvidenceForm()

    return render(request, 'tracker/pole_detail.html', {'pole': pole, 'stages': stages, 'evidence_map': evidence_map, 'form': form})


@login_required
def delete_evidence(request, evidence_id):
    evidence = get_object_or_404(Evidence, id=evidence_id)
    check_project_access(request.user, evidence.pole.project)  # <--- SECURITY CHECK
    
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
    # Already protected by @staff_member_required
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
@rate_limit(limit=10, period=60)
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

@rate_limit(limit=60, period=60)
def client_city_view(request, client_uuid):
    project = get_object_or_404(Project, client_uuid=client_uuid)
    
    # 1. Base Query with Eager Loading (Fixes N+1)
    # We prefetch 'evidence' (images) and 'custom_values' (metadata) so they don't trigger new queries.
    poles = project.poles.select_related('project') \
        .prefetch_related(
            'evidence__stage',          # For image captions
            'custom_values__field_def', # For metadata labels
            'issues'                    # For flag icons
        ).order_by('id')

    # 2. Stats Calculation (Efficient Aggregation)
    total_poles = poles.count()
    completed_poles = poles.filter(is_completed=True).count()
    progress = int((completed_poles / total_poles) * 100) if total_poles > 0 else 0

    # 3. Dynamic Filter Options
    # We find the "Grouping Key" (e.g., Village) and get all unique values for the dropdown
    group_def = project.field_definitions.filter(is_grouping_key=True).first()
    filter_options = []
    if group_def:
        # Get distinct values for this field efficiently
        filter_options = ItemFieldValue.objects.filter(
            pole__project=project, 
            field_def=group_def
        ).values_list('value', flat=True).distinct().order_by('value')

    # 4. Apply Filtering
    current_filter = request.GET.get('village', '')
    if current_filter and group_def:
        poles = poles.filter(custom_values__field_def=group_def, custom_values__value=current_filter)

    # 5. Pagination (50 items per page)
    paginator = Paginator(poles, 50)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    print(f"DEBUG: Client View - Total Poles: {total_poles}")
    print(f"DEBUG: Client View - Page Obj Count: {len(page_obj)}")
    print(f"DEBUG: Client View - Current Filter: {current_filter}")

    return render(request, 'tracker/client_city_view.html', {
        'project': project,
        'page_obj': page_obj,           # The paginated list of poles
        'total_poles': total_poles,
        'completed_poles': completed_poles,
        'progress': progress,
        'filter_options': filter_options,
        'current_filter': current_filter
    })

@rate_limit(limit=5, period=300) # Strict limit: 5 reports per 5 mins
def report_issue(request, pole_id):
    pole = get_object_or_404(Pole, id=pole_id)
    
    # [FIX] SECURITY: Ensure we don't accept reports on archived projects
    if pole.project.status != 'ACTIVE':
         messages.error(request, "This project is closed. Issues cannot be reported.")
         return redirect('client_view', client_uuid=pole.project.client_uuid)

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
    check_project_access(request.user, project)  # <--- SECURITY CHECK
    
    issues = ProjectIssue.objects.filter(pole__project=project, status='OPEN').order_by('-created_at')
    return render(request, 'tracker/project_issues.html', {'project': project, 'issues': issues})

@login_required
def resolve_issue(request, issue_id):
    issue = get_object_or_404(ProjectIssue, id=issue_id)
    check_project_access(request.user, issue.pole.project)  # <--- SECURITY CHECK
    
    issue.status = 'RESOLVED'
    issue.save()
    
    log_action(issue.pole.project, request.user, "Resolved Issue", issue.pole.identifier, f"Resolved report from {issue.reported_by}")
    
    messages.success(request, "Issue marked as resolved.")
    return redirect('project_issues', project_id=issue.pole.project.id)

    # [FIX] SECURITY: REMOVED create_admin_temp COMPLETELY