from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import User, ProjectType, StageDefinition, Project, Pole, Evidence, ItemFieldDefinition, ItemFieldValue, Client
from .forms import ItemFieldDefinitionForm 
from .models import ProjectIssue

class CustomUserAdmin(UserAdmin):
    model = User
    fieldsets = UserAdmin.fieldsets + (('Role Configuration', {'fields': ('role',)}),)
    add_fieldsets = UserAdmin.add_fieldsets + (('Role Configuration', {'fields': ('role',)}),)
admin.site.register(User, CustomUserAdmin)

# NEW: Client Admin
@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    list_display = ('name', 'created_at', 'get_link')
    def get_link(self, obj): return f"/client/{obj.uuid}/"
    get_link.short_description = "Client Dashboard Link"

class StageDefinitionInline(admin.TabularInline):
    model = StageDefinition
    extra = 1

@admin.register(ProjectType)
class ProjectTypeAdmin(admin.ModelAdmin):
    list_display = ('name', 'unit_name', 'description')
    inlines = [StageDefinitionInline]

class ItemFieldDefinitionInline(admin.TabularInline):
    model = ItemFieldDefinition
    form = ItemFieldDefinitionForm 
    extra = 1
    fields = ('label', 'field_type', 'excel_column', 'is_grouping_key') 
    def get_formset(self, request, obj=None, **kwargs):
        formset = super().get_formset(request, obj, **kwargs)
        formset.form.parent_project = obj 
        return formset

@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ('name', 'client', 'project_type', 'status')
    list_filter = ('client', 'status', 'project_type')
    filter_horizontal = ('contractors',) 
    inlines = [ItemFieldDefinitionInline]
    
    fieldsets = (
        (None, {'fields': ('name', 'client', 'project_type', 'status')}),
        ('Assignments', {'fields': ('contractors',)}),
        ('Data Source', {'fields': ('data_file',)}),
    )

# Add this to admin.py

@admin.register(ProjectIssue)
class ProjectIssueAdmin(admin.ModelAdmin):
    list_display = ('pole', 'message', 'status', 'created_at')
    list_filter = ('status', 'created_at')
    actions = ['mark_resolved']

    def mark_resolved(self, request, queryset):
        queryset.update(status='RESOLVED')

admin.site.register(Pole)
admin.site.register(Evidence)
admin.site.register(ItemFieldValue)