import factory.django

from packages.models import Project, Release
from users.tests.factories import UserFactory


class ProjectFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Project

    owner = factory.SubFactory(UserFactory)

    name = factory.Sequence(lambda n: f"project-{n}")
    kind = Project.Kind.quark


class ReleaseFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Release

    project = factory.SubFactory(ProjectFactory)
    owner = factory.SubFactory(UserFactory)
    version = factory.Sequence(lambda n: f"1.0.{n}")
