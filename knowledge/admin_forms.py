"""Forms for the existing Django Admin; public scoring remains unchanged."""
from decimal import Decimal

from django import forms
from django.contrib.admin.widgets import RelatedFieldWidgetWrapper
from django.core.exceptions import ValidationError
from django.forms.models import BaseInlineFormSet
from django.utils.text import slugify

from courses.models import Course
from .models import Assessment, CourseRecommendation, Option, Passage, Question, ResultBand
from .numbers import parse_number


def suggested_slug(title):
    source = "абвгґдеєжзиіїйклмнопрстуфхцчшщьюя"
    target = ("a", "b", "v", "h", "g", "d", "e", "ye", "zh", "z", "y", "i", "yi", "y", "k", "l", "m", "n", "o", "p", "r", "s", "t", "u", "f", "kh", "ts", "ch", "sh", "shch", "", "yu", "ya")
    return slugify(title.casefold().translate(str.maketrans(dict(zip(source, target)))))[:90].strip("-") or "test"


def audience_errors(test):
    errors = []
    if test.subject == "math" and test.category != "general":
        errors.append("Для математики оберіть напрям перевірки «Загальні навички». Граматика, лексика й читання призначені для англійської.")
    if test.grade and test.grade_band and test.effective_band != test.grade_band:
        errors.append("Конкретний клас не відповідає навчальному напряму: 1–4, 5–9 або НМТ для 10–11 класів.")
    return errors


class UkrainianChoicesMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            empty = "— Оберіть варіант —" if field.required else "— Не обрано —"
            if isinstance(field, forms.ModelChoiceField):
                if field.empty_label is not None:
                    field.empty_label = empty
            elif isinstance(field, forms.ChoiceField):
                field.choices = [(value, empty if value == "" else label) for value, label in field.choices]


class PassageRelatedWidget(RelatedFieldWidgetWrapper):
    """Keep Django's supported related-object popup with the test preselected."""
    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        if self.assessment_id:
            context["url_params"] += f"&assessment={self.assessment_id}"
        return context


class AssessmentAdminForm(UkrainianChoicesMixin, forms.ModelForm):
    class Meta:
        model = Assessment
        exclude = ("is_published",)
        labels = {"description": "Короткий опис", "grade_band": "Навчальний напрям", "audience": "Клас, рівень або аудиторія", "slug": "URL — адреса тесту", "seo_description": "Опис для пошукових систем (meta description)"}
        widgets = {name: forms.Textarea(attrs={"rows": rows, "cols": 75}) for name, rows in (("description", 3), ("methodology_notes", 4), ("source_notes", 3))}
        help_texts = {
            "category": "Для англійської: загальні навички, граматика, лексика або читання. Для математики — загальні навички.",
            "grade_band": "Оберіть вікову групу або підготовку до НМТ. Для загального тесту англійської без прив’язки до класу можна залишити порожнім.",
            "grade": "Необов’язково. Наприклад, 7 — для тесту саме за програмою 7 класу.",
            "audience": "Наприклад: початкові навички, учні 7 класу або підготовка до НМТ. Це опис аудиторії, а не підтверджений рівень.",
            "slug": "Пропонується з назви; можна змінити вручну. Якщо залишити порожнім, адреса створиться під час збереження. Перейменування збереженого тесту не змінює його адресу.",
            "question_count": "У спробу потрапляють перші N активних запитань за порядком. Зайві запитання залишаються в банку тесту.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.was_published = bool(self.instance.is_published)
        if "slug" in self.fields:
            self.fields["slug"].required = False

    def clean_slug(self):
        value = self.cleaned_data.get("slug")
        # Model field order places slug before subject even when Admin displays it later.
        subject = self.cleaned_data.get("subject", self.data.get(self.add_prefix("subject")))
        others = Assessment.objects.filter(subject=subject).exclude(pk=self.instance.pk)
        if value:
            if others.filter(slug=value).exists():
                raise ValidationError("Тест із такою адресою для цього предмета вже існує. Змініть URL.")
            return value
        # A cleared existing field retains its URL; automatic generation is only for new tests.
        if self.instance.pk:
            return self.instance.slug
        base = suggested_slug(self.cleaned_data.get("title", ""))
        if base in {"nmt", "sitemap", "attempt"}:
            base += "-test"
        value, suffix = base, 2
        while others.filter(slug=value).exists():
            value, suffix = f"{base}-{suffix}", suffix + 1
        return value

    def clean(self):
        data = super().clean()
        subject, category = data.get("subject"), data.get("category")
        if subject == "math" and category and category != "general":
            self.add_error("category", "Для математики оберіть «Загальні навички».")
        return data

    def _post_clean(self):
        # Validate edited metadata now; validate publication after all inlines have saved.
        self.instance.is_published = False
        try:
            super()._post_clean()
        finally:
            self.instance.is_published = self.was_published


class QuestionAdminForm(UkrainianChoicesMixin, forms.ModelForm):
    numeric_answer = forms.CharField(label="Правильна числова відповідь", required=False, widget=forms.TextInput(attrs={"inputmode": "decimal", "size": 22}), help_text="Кома або крапка. Наприклад: −2,5. Без дробів 1/2, формул і розділювачів тисяч.")
    tolerance = forms.CharField(label="Допустима абсолютна похибка", initial="0", widget=forms.TextInput(attrs={"inputmode": "decimal", "size": 22}), help_text="0 — точний збіг; 0,01 — відхилення не більше 0,01. Від’ємна похибка заборонена.")

    class Meta:
        model = Question
        fields = "__all__"
        widgets = {"prompt": forms.Textarea(attrs={"rows": 4, "cols": 75}), "explanation": forms.Textarea(attrs={"rows": 4, "cols": 75})}
        help_texts = {"order": "Позиція у тесті. Менші числа показуються раніше.", "is_active": "Неактивне запитання зберігається, але не входить до нових спроб.", "passage": "Спочатку збережіть текст для читання цього тесту. Він може бути спільним для кількох запитань.", "explanation": "Учень побачить пояснення лише після завершення тесту. Поясніть, чому відповідь правильна."}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("prompt", "explanation"):
            if name in self.fields:
                self.fields[name].required = True
        if not self.is_bound:
            for name in ("numeric_answer", "tolerance"):
                value = self.initial.get(name)
                if isinstance(value, Decimal):
                    self.initial[name] = format(value.normalize(), "f")
        if "passage" not in self.fields:
            return  # Standard Admin view-only forms have no editable fields.
        assessment_id = self.data.get("assessment") if self.is_bound else self.initial.get("assessment", self.instance.assessment_id)
        try:
            test = Assessment.objects.filter(pk=int(assessment_id)).first()
        except (ValueError, TypeError, OverflowError):
            test = None
        self.fields["passage"].queryset = Passage.objects.filter(assessment=test, assessment__subject="english") if test else Passage.objects.none()
        widget = self.fields["passage"].widget
        if isinstance(widget, RelatedFieldWidgetWrapper):
            wrapper = PassageRelatedWidget(widget.widget, widget.rel, widget.admin_site,
                can_add_related=widget.can_add_related and bool(test and test.subject == "english"),
                can_change_related=widget.can_change_related, can_delete_related=widget.can_delete_related,
                can_view_related=widget.can_view_related)
            wrapper.assessment_id = test.pk if test else None
            self.fields["passage"].widget = wrapper

    def clean_numeric_answer(self):
        raw = self.cleaned_data["numeric_answer"].strip()
        if self.cleaned_data.get("kind") == "numeric" and not raw:
            raise ValidationError("Для числового завдання вкажіть правильне число.")
        return parse_number(raw) if raw else None

    def clean_tolerance(self):
        value = parse_number(self.cleaned_data["tolerance"])
        if value < 0:
            raise ValidationError("Похибка має бути 0 або додатним числом.")
        return value


class OptionAdminForm(forms.ModelForm):
    order = forms.IntegerField(label="Порядок", min_value=1, initial=1)

    class Meta:
        model = Option
        fields = "__all__"
        widgets = {"text": forms.Textarea(attrs={"rows": 2, "cols": 48})}


class OptionFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return
        options = [form.cleaned_data for form in self.forms if form.cleaned_data and not form.cleaned_data.get("DELETE")]
        if self.instance.kind == "numeric":
            if options:
                raise ValidationError("Числове завдання не має варіантів відповіді. Позначте наявні варіанти для видалення; число й похибка задаються вище.")
            return
        if not 2 <= len(options) <= 10:
            raise ValidationError("Додайте від 2 до 10 варіантів відповіді.")
        texts = [option["text"].strip() for option in options]
        if len(set(texts)) != len(texts):
            raise ValidationError("Варіанти відповіді не повинні повторюватися.")
        correct = sum(option.get("is_correct", False) for option in options)
        if self.instance.kind == "single" and correct != 1:
            raise ValidationError("Позначте рівно один правильний варіант для одиночного вибору.")
        if self.instance.kind == "multiple" and correct < 2:
            raise ValidationError("Для множинного вибору позначте щонайменше два правильні варіанти.")


class BandAdminForm(UkrainianChoicesMixin, forms.ModelForm):
    class Meta:
        model = ResultBand
        fields = "__all__"
        labels = {"minimum_percent": "Нижня межа, % (включно)", "feedback": "Інтерпретація та що варто повторити"}
        help_texts = {"minimum_percent": "Перший діапазон починається з 0. Верхня межа — наступний поріг; останній діапазон включає 100%.", "feedback": "Поясніть результат і порадьте теми для повторення. Не обіцяйте точний бал НМТ."}
        widgets = {"feedback": forms.Textarea(attrs={"rows": 3, "cols": 75})}


class BandFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return
        bands = [f.cleaned_data for f in self.forms if f.cleaned_data and not f.cleaned_data.get("DELETE")]
        if bands and min(b["minimum_percent"] for b in bands) != 0:
            raise ValidationError("Перший діапазон результатів має починатися з 0%, щоб кожен учасник отримав інтерпретацію.")
        if self.instance.level_estimation_enabled and (not bands or any(not b.get("level_hint") for b in bands)):
            raise ValidationError("Для мовної шкали потрібні діапазони з орієнтовним рівнем у кожному. Без методичної перевірки вимкніть мовну оцінку.")


class RecommendationAdminForm(UkrainianChoicesMixin, forms.ModelForm):
    class Meta:
        model = CourseRecommendation
        fields = "__all__"
        labels = {"explanation": "Текст рекомендації"}
        help_texts = {"minimum_percent": "Нижня межа включно.", "maximum_percent": "Верхня межа включно. 0–100 — рекомендувати незалежно від відсотка.", "course": "Тільки відповідний предмет і вікова група. Ціни тут не змінюються."}
        widgets = {"explanation": forms.Textarea(attrs={"rows": 2, "cols": 42})}

    def __init__(self, *args, assessment=None, **kwargs):
        super().__init__(*args, **kwargs)
        if "course" not in self.fields:
            return
        if assessment is None:
            assessment_id = self.data.get("assessment") if self.is_bound else self.initial.get("assessment", self.instance.assessment_id)
            try:
                assessment = Assessment.objects.filter(pk=int(assessment_id)).first()
            except (ValueError, TypeError, OverflowError):
                assessment = None
        courses = Course.objects.all()
        if assessment and assessment.subject:
            courses = courses.filter(subject=assessment.subject)
            if assessment.effective_band:
                courses = courses.filter(grade_band=assessment.effective_band)
        self.fields["course"].queryset = courses
        widget = self.fields["course"].widget
        if isinstance(widget, RelatedFieldWidgetWrapper):
            widget.can_add_related = False
            widget.can_change_related = False
            widget.can_delete_related = False
            widget.can_view_related = False


class PassageAdminForm(UkrainianChoicesMixin, forms.ModelForm):
    class Meta:
        model = Passage
        fields = "__all__"
        widgets = {"text": forms.Textarea(attrs={"rows": 12, "cols": 85})}
        help_texts = {"text": "Авторський або дозволений до використання текст. Після збереження оберіть його в запитаннях цього тесту."}

    def clean_assessment(self):
        test = self.cleaned_data["assessment"]
        if test.subject != "english":
            raise ValidationError("Тексти для читання додаються до тестів з англійської мови.")
        if self.instance.pk and self.instance.questions.exclude(assessment=test).exists():
            raise ValidationError("Цей текст уже використовується. Спочатку змініть прив’язку відповідних запитань.")
        return test
