from django.apps import AppConfig


class UsersConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "users"

    def ready(self):
        from django.contrib.auth import get_user_model
        from django.db.models import CharField

        from users.validators import validate_username

        # Django's `User` can't be changed, so its username field gets the rules here.
        # allauth's signup, the admin's forms and `createsuperuser` all run the
        # field's validators.
        field = get_user_model()._meta.get_field("username")
        assert isinstance(field, CharField)
        if validate_username not in field.validators:
            field.validators.append(validate_username)
