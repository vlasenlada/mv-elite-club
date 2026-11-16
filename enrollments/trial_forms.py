from django import forms

from courses.models import Course
from .trial_models import normalize_phone


class TrialRequestForm(forms.Form):
    student_name = forms.CharField(label="Ім’я учня", max_length=120, widget=forms.TextInput(attrs={"autocomplete": "name"}))
    level = forms.CharField(label="Клас або рівень підготовки", max_length=100)
    age_category = forms.ChoiceField(label="Вік учня", choices=[("", "Оберіть варіант"), ("minor", "До 18 років"), ("adult", "18 років або більше")])
    guardian_name = forms.CharField(label="Ім’я одного з батьків / законного представника", max_length=120, required=False, help_text="Обов’язково для неповнолітнього учня.")
    email = forms.EmailField(label="Контактна електронна пошта", widget=forms.EmailInput(attrs={"autocomplete": "email"}))
    phone = forms.CharField(label="Номер телефону", max_length=40, widget=forms.TextInput(attrs={"type": "tel", "autocomplete": "tel"}))
    requested_format = forms.ChoiceField(label="Бажаний формат навчання", choices=Course.LessonFormat.choices)
    comment = forms.CharField(label="Коментар (необов’язково)", max_length=1500, required=False, widget=forms.Textarea(attrs={"rows": 4}), help_text="Можна вказати зручний час для зв’язку. Не додавайте медичних даних, документів чи паролів.")
    privacy_consent = forms.BooleanField(label="Погоджуюся на обробку вказаних даних для розгляду заявки та зв’язку щодо пробного заняття.")
    guardian_consent = forms.BooleanField(label="Для неповнолітнього: я є одним із батьків / законним представником і погоджую подання даних дитини для організації пробного.", required=False)
    website = forms.CharField(required=False, widget=forms.HiddenInput)

    def __init__(self, *args, course, user, **kwargs):
        initial = dict(kwargs.pop("initial", {}))
        if user.is_authenticated:
            initial.update(student_name=user.get_full_name(), email=user.email)
        super().__init__(*args, initial=initial, **kwargs)
        if course.subject == Course.Subject.MATH:
            self.fields["level"].help_text = "Наприклад: 7 клас, потрібна допомога з алгеброю"
        elif course.subject == Course.Subject.ENGLISH:
            self.fields["level"].help_text = ("Наприклад: 7 клас, рівень A1–A2" if course.grade_band else
                "Наприклад: початковий рівень, раніше не вивчав(-ла) англійську")
        else:
            self.fields["level"].help_text = "Вкажіть клас і теми, з якими потрібна допомога."
        age = self.data.get("age_category") if self.is_bound else self.initial.get("age_category", "")
        self.guardian_fields_hidden = age == "adult"
        for name in ("guardian_name", "guardian_consent"):
            self.fields[name].required = age == "minor"
        self.fields["guardian_name"].error_messages["required"] = "Вкажіть ім’я одного з батьків або законного представника."
        self.fields["guardian_consent"].error_messages["required"] = "Для неповнолітнього потрібне підтвердження законного представника."
        if self.is_bound and self.guardian_fields_hidden:
            # A hidden field is not a security boundary. Discard irrelevant data
            # server-side, including stale values or deliberately forged input.
            self.data = self.data.copy()
            self.data["guardian_name"] = ""
            self.data["guardian_consent"] = ""
        self.fields["requested_format"].choices = [
            (key, label) for key, label in Course.LessonFormat.choices
            if (course.supports_individual if key == "individual" else course.supports_group)
        ]

    def clean_phone(self):
        return normalize_phone(self.cleaned_data["phone"])

    def clean(self):
        data = super().clean()
        if data.get("age_category") == "minor":
            if not data.get("guardian_name") and "guardian_name" not in self.errors:
                self.add_error("guardian_name", "Вкажіть ім’я одного з батьків або законного представника.")
            if not data.get("guardian_consent") and "guardian_consent" not in self.errors:
                self.add_error("guardian_consent", "Для неповнолітнього потрібне підтвердження законного представника.")
        elif data.get("age_category") == "adult":
            data["guardian_name"] = ""
            data["guardian_consent"] = False
        return data
