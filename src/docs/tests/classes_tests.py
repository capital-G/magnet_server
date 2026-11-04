"""Links from classes to their docs: the Classes tab of the project page and the class
search (`docs.status.add_docs_urls`)."""

import pytest
from django.urls import reverse
from django.utils import timezone
from pytest_django.asserts import assertContains, assertNotContains

from docs import publish
from docs.models import DocBuild
from packages.models import ClassDefinition, MethodDefinition
from packages.tests.factories import ProjectFactory, ReleaseFactory

FOO_DOCS = "http://docs.test/foo/Classes/Foo.html"


@pytest.fixture
def release(settings):
    settings.MAGNET_DOCS_BASE_URL = "http://docs.test/"
    release = ReleaseFactory.create(
        project=ProjectFactory.create(name="Foo"),
        version="1.0.0",
        final=True,
        classes_extracted_at=timezone.now(),
    )
    foo = define(release, "Foo")
    MethodDefinition.objects.create(class_definition=foo, name="shout")
    MethodDefinition.objects.create(
        class_definition=foo, name="new", is_class_method=True
    )
    # no page
    define(release, "FooHelper")
    # an extension of SC's String, whose page isn't the package's
    define(release, "String", is_extension=True)
    return release


def define(release, name: str, **kwargs) -> ClassDefinition:
    return ClassDefinition.objects.create(
        release=release, name=name, path=f"Classes/{name}.sc", line=1, **kwargs
    )


def documented(release, published: bool = True) -> DocBuild:
    build = DocBuild.objects.create(
        release=release,
        status=DocBuild.Status.built,
        documents=["Classes/Foo", "Classes/String", "Guides/Intro"],
    )
    if published:
        folder = publish.build_dir("foo", build.pk)
        folder.mkdir(parents=True)
        publish.publish("foo", folder)
    return build


def classes_tab(client, **query):
    return client.get(reverse("project-detail", args=["foo"]), query)


def search(client, query: str):
    return client.get(reverse("class-search"), {"q": query})


@pytest.mark.django_db
def test_classes_tab_links_documented_classes(client, release):
    documented(release)

    response = classes_tab(client)

    assertContains(response, f'href="{FOO_DOCS}">docs</a>', count=1)
    assertNotContains(response, "Classes/FooHelper.html")
    assertNotContains(response, "Classes/String.html")


@pytest.mark.django_db
def test_class_search_links_classes_and_methods(client, release):
    documented(release)

    assertContains(search(client, "Foo"), f'href="{FOO_DOCS}">docs</a>', count=1)
    assertContains(search(client, "shout"), f'href="{FOO_DOCS}#-shout">docs</a>')
    assertContains(search(client, "new"), f'href="{FOO_DOCS}#*new">docs</a>')
    assertNotContains(search(client, "String"), "docs</a>")


@pytest.mark.django_db
def test_only_the_documented_version_links(client, release):
    older = ReleaseFactory.create(
        project=release.project,
        version="0.9.0",
        final=True,
        classes_extracted_at=timezone.now(),
    )
    define(older, "Foo")
    documented(release)

    assertNotContains(classes_tab(client, version="0.9.0"), FOO_DOCS)
    assertContains(classes_tab(client, version="1.0.0"), FOO_DOCS)


@pytest.mark.django_db
def test_no_links_without_published_docs(client, release):
    documented(release, published=False)

    assertNotContains(classes_tab(client), FOO_DOCS)
    assertNotContains(search(client, "Foo"), FOO_DOCS)


@pytest.mark.django_db
def test_the_newest_built_build_counts(client, release):
    documented(release)
    # a later build without the page (e.g. the help file was removed)
    DocBuild.objects.create(
        release=release, status=DocBuild.Status.built, documents=["Guides/Intro"]
    )

    assertNotContains(classes_tab(client), FOO_DOCS)
