from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from .models import Project
from django.contrib.auth import get_user_model  # <--- ADD THIS
from django.http import HttpResponse

@login_required
def contractor_dashboard(request):
    # Only show projects assigned to this specific worker
    my_projects = Project.objects.filter(contractors=request.user)
    
    return render(request, 'tracker/dashboard.html', {'projects': my_projects})


from django.shortcuts import render, get_object_or_404 # <-- Add get_object_or_404
from django.contrib.auth.decorators import login_required
from .models import Project, Pole # <-- Make sure Pole is imported

# ... keep your existing contractor_dashboard view ...

@login_required
def project_detail(request, project_id):
    # 1. Get the project (or show 404 error if it doesn't exist)
    project = get_object_or_404(Project, id=project_id)
    
    # 2. Get all poles for this project
    poles = project.poles.all()
    
    return render(request, 'tracker/project_detail.html', {
        'project': project, 
        'poles': poles
    })


from django.shortcuts import redirect
from .forms import EvidenceForm # <-- Import the form we just made
from .models import Project, Pole, StageDefinition, Evidence # <-- Update imports

# ... (keep existing views) ...

@login_required
def pole_detail(request, pole_id):
    pole = get_object_or_404(Pole, id=pole_id)
    stages = pole.project.project_type.stages.all()
    existing_evidence = Evidence.objects.filter(pole=pole)
    evidence_map = {e.stage.id: e for e in existing_evidence}

    if request.method == 'POST':
        form = EvidenceForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                evidence = form.save(commit=False)
                evidence.pole = pole
                stage_id = request.POST.get('stage_id')
                
                if stage_id:
                    evidence.stage = get_object_or_404(StageDefinition, id=stage_id)
                    evidence.save()
                    
                    # --- DEBUGGING MATH START ---
                    total_required = stages.count()
                    stages_done = Evidence.objects.filter(pole=pole).values('stage').distinct().count()
                    
                    print(f"--------------------------------------------------")
                    print(f"DEBUG: Pole needs {total_required} stages.")
                    print(f"DEBUG: You have completed {stages_done} stages.")
                    
                    if stages_done >= total_required:
                        print("DEBUG: MATH MATCHES! Marking as Complete.")
                        pole.is_completed = True
                        pole.save()
                    else:
                        print(f"DEBUG: STILL WAITING for {total_required - stages_done} more photos.")
                    print(f"--------------------------------------------------")
                    # --- DEBUGGING MATH END ---

                    return redirect('pole_detail', pole_id=pole.id)
            except Exception as e:
                print(f"Error: {e}")
    else:
        form = EvidenceForm()

    return render(request, 'tracker/pole_detail.html', {
        'pole': pole,
        'stages': stages,
        'evidence_map': evidence_map,
        'form': form
    })

from django.contrib.admin.views.decorators import staff_member_required

# ... (keep your existing code above) ...

@staff_member_required
def admin_project_inspection(request, project_id):
    project = get_object_or_404(Project, id=project_id)
    poles = project.poles.all()
    
    # Organize data: { Pole: [Photo1, Photo2, Photo3...] }
    inspection_data = {}
    
    for pole in poles:
        # Get all photos for this pole, sorted by stage order
        photos = Evidence.objects.filter(pole=pole).order_by('stage__order')
        inspection_data[pole] = photos

    return render(request, 'tracker/admin_inspection.html', {
        'project': project,
        'inspection_data': inspection_data
    })

def client_view(request, client_uuid):
    project = get_object_or_404(Project, client_uuid=client_uuid)
    poles = project.poles.all()
    
    # Calculate Progress
    total_poles = poles.count()
    completed_poles = poles.filter(is_completed=True).count()
    progress = int((completed_poles / total_poles) * 100) if total_poles > 0 else 0

    # --- THE FIX: USE A LIST INSTEAD OF A DICT ---
    pole_list = []
    for pole in poles:
        history = pole.evidence.all().order_by('stage__order')
        pole_list.append({
            'pole_obj': pole,   # We give it a clear name
            'history': history
        })
    # ---------------------------------------------

    return render(request, 'tracker/client_view.html', {
        'project': project,
        'pole_list': pole_list, # Send the list
        'progress': progress
    })

# TEMPORARY FUNCTION - DELETE AFTER USE
def create_admin_user(request):
    User = get_user_model()  # <--- This asks Django for the CORRECT user model
    
    if not User.objects.filter(username='admin').exists():
        # Create the superuser
        User.objects.create_superuser('admin', 'admin@example.com', 'admin123')
        return HttpResponse("SUCCESS! User: 'admin' | Password: 'admin123' created.")
    else:
        return HttpResponse("User 'admin' already exists.")