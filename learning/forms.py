
from django import forms

from .models import Submission


class SubmissionForm(forms.ModelForm):
    class Meta:
        model = Submission

        fields = ["answer_text"]

        labels = {
            "answer_text": "Твоя відповідь"
        }

        widgets = {
            "answer_text": forms.Textarea(
                attrs={
                    "class": "homework-answer-input",
                    "placeholder": "Напиши свою відповідь тут...",
                    "rows": 8,
                }
            )
        }
