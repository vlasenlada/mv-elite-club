from django import forms

from .numbers import parse_number


class AnswerForm(forms.Form):
    def __init__(self, *args, question, **kwargs):
        super().__init__(*args, **kwargs)
        self.kind = question["kind"]
        if self.kind == "numeric":
            self.fields["answer"] = forms.CharField(label="Ваша відповідь", required=False, max_length=26,
                help_text="Кома або крапка — десятковий роздільник. Без розділювачів тисяч, дробів і формул.",
                widget=forms.TextInput(attrs={"inputmode": "decimal", "autocomplete": "off"}))
        else:
            choices = [(option["id"], option["text"]) for option in question["options"]]
            if self.kind == "multiple":
                self.fields["answer"] = forms.MultipleChoiceField(label="Оберіть усі правильні відповіді", choices=choices, required=False, widget=forms.CheckboxSelectMultiple)
            else:
                self.fields["answer"] = forms.ChoiceField(label="Оберіть одну відповідь", choices=choices, required=False, widget=forms.RadioSelect)

    def clean_answer(self):
        value = self.cleaned_data["answer"]
        if self.kind == "numeric":
            return str(parse_number(value)) if value.strip() else None
        return list(dict.fromkeys(value)) if self.kind == "multiple" else [value] if value else []
