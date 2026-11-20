from django.db.models.signals import pre_save, post_save, post_delete
from django.utils import timezone

from .models import Assessment, CourseRecommendation, Option, Passage, Question, ResultBand


CONTENT_MODELS = (Question, Option, Passage, ResultBand, CourseRecommendation)


def before(sender, instance, **kwargs):
    instance._knowledge_previous = sender.objects.filter(pk=instance.pk).values().first() if instance.pk else None


def unpublish(sender, instance, **kwargs):
    current = sender.objects.filter(pk=instance.pk).values().first()
    previous = getattr(instance, "_knowledge_previous", None)
    if current == previous and current is not None:
        return
    ids = {getattr(instance, "assessment_id", None)}
    if sender is Option:
        ids = set(Question.objects.filter(pk=instance.question_id).values_list("assessment_id", flat=True))
        if previous:
            ids.update(Question.objects.filter(pk=previous["question_id"]).values_list("assessment_id", flat=True))
    elif previous:
        ids.add(previous["assessment_id"])
    Assessment.objects.filter(pk__in=[pk for pk in ids if pk]).update(is_published=False, updated_at=timezone.now())


for model in CONTENT_MODELS:
    pre_save.connect(before, sender=model, dispatch_uid=f"knowledge.before.{model.__name__}")
    post_save.connect(unpublish, sender=model, dispatch_uid=f"knowledge.save.{model.__name__}")
    post_delete.connect(unpublish, sender=model, dispatch_uid=f"knowledge.delete.{model.__name__}")
