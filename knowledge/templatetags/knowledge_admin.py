from django import template

register = template.Library()


@register.filter
def knowledge_first(apps):
    """Reorder this app only; preserve Django's navigation for every other app."""
    result = []
    for app in apps:
        if app["app_label"] == "knowledge":
            app = {**app, "models": sorted(app["models"], key=lambda model: model["object_name"] != "Assessment")}
        result.append(app)
    return result
