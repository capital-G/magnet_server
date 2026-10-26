import factory.django
import faker
from django.contrib.auth.models import User

from users.models import UserProfile

fake = faker.Faker()


class UserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = User

    username = factory.LazyAttribute(lambda _: fake.user_name())
    email = factory.LazyAttribute(lambda _: fake.email())
    # hashed when built; a post-generation `set_password` wasn't saved
    password = factory.django.Password("password")


class UserProfileFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = UserProfile

    user = factory.SubFactory(UserFactory)
