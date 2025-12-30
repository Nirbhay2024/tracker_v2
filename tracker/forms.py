from django import forms
from .models import Evidence, ItemFieldDefinition
from .utils import get_dropdown_options, get_file_headers

class EvidenceForm(forms.ModelForm):
    class Meta:
        model = Evidence
        fields = ['image', 'gps_lat', 'gps_long']
        widgets = {
            'gps_lat': forms.HiddenInput(),
            'gps_long': forms.HiddenInput(),
        }

class DynamicItemForm(forms.Form):
    def __init__(self, project, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field_def in project.field_definitions.all():
            field_name = f"custom_{field_def.id}"
            if field_def.field_type == 'TEXT':
                self.fields[field_name] = forms.CharField(label=field_def.label, required=True, widget=forms.TextInput(attrs={'class': 'form-control'}))
            elif field_def.field_type == 'DROPDOWN':
                choices = [('', '-- Select --')] + get_dropdown_options(project.data_file, field_def.excel_column)
                self.fields[field_name] = forms.ChoiceField(label=field_def.label, choices=choices, required=True, widget=forms.Select(attrs={'class': 'form-select'}))

class ItemFieldDefinitionForm(forms.ModelForm):
    excel_column = forms.ChoiceField(required=False)

    class Meta:
        model = ItemFieldDefinition
        fields = '__all__' # This will now include is_grouping_key automatically

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        project = None
        if self.instance and self.instance.pk:
            project = self.instance.project
        if not project and hasattr(self, 'parent_project'):
            project = self.parent_project

        if project and project.data_file:
            headers = get_file_headers(project.data_file)
            if headers:
                self.fields['excel_column'].choices = [('', '-- Select Column --')] + [(h, h) for h in headers]
            else:
                self.fields['excel_column'].choices = [('', 'No headers found')]
        else:
            self.fields['excel_column'].choices = [('', 'Save Project & Upload File First')]


# Add to forms.py
class IssueForm(forms.Form):
    message = forms.CharField(widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 3, 'placeholder': 'Describe the issue (e.g. Photo is blurry, Pole is leaning)...'}))