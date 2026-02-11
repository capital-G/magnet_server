import factory.django
import faker
from django.utils.text import slugify

from packages.models import Project, Release
from users.tests.factories import UserFactory

fake = faker.Faker()


class ProjectFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Project

    owner = factory.SubFactory(UserFactory)

    name = factory.Sequence(lambda n: f"project {n}")
    slug = factory.LazyAttribute(lambda obj: slugify(obj.name))


class ReleaseFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Release

    project = factory.SubFactory(ProjectFactory)
    owner = factory.SubFactory(UserFactory)
