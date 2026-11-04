import factory.django
import faker
from django.contrib.auth.models import User

from users.models import UserProfile

fake = faker.Faker()


class UserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = User

    # valid for `users.validators.validate_username` (faker's may contain dots)
    username = factory.Sequence(lambda n: f"user{n}")
    email = factory.LazyAttribute(lambda _: fake.email())
    # hashed when built; a post-generation `set_password` wasn't saved
    password = factory.django.Password("password")


class UserProfileFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = UserProfile

    user = factory.SubFactory(UserFactory)
