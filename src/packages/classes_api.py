"""The classes and methods of packages for the CLI: search, the conflicts of a
package, and a check of a quark artifact before publishing (nothing is stored)."""

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers
from rest_framework.generics import get_object_or_404
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from packages.api import BearerTokenAuthentication, HasPublishScope, read_upload
from packages.archive import read_archive
from packages.classes import (
    Definition,
    NameConflict,
    PackageConflicts,
    conflicts_by_package,
    current_release,
    find_conflicts,
    group_by_package,
    read_class_files,
    search,
)
from packages.models import ClassDefinition, MethodDefinition, Project
from packages.validators import normalize_name


class ClassHitSerializer(serializers.Serializer):
    name = serializers.CharField()
    extension = serializers.BooleanField()
    package = serializers.CharField(help_text="normalised name")
    version = serializers.CharField()
    path = serializers.CharField(help_text="the file in the quark artifact")


class MethodHitSerializer(ClassHitSerializer):
    class_method = serializers.BooleanField()


# `class` is a keyword, so it can't be declared in the class body
MethodHitSerializer._declared_fields["class"] = serializers.CharField(
    help_text="the class the method belongs to (or extends)"
)


class SearchSerializer(serializers.Serializer):
    classes = ClassHitSerializer(many=True)
    methods = MethodHitSerializer(many=True)
    truncated = serializers.BooleanField(
        help_text="more than 50 classes or methods matched"
    )


class ConflictingMethodSerializer(serializers.Serializer):
    name = serializers.CharField()
    class_method = serializers.BooleanField()


ConflictingMethodSerializer._declared_fields["class"] = serializers.CharField()


class PackageConflictsSerializer(serializers.Serializer):
    package = serializers.CharField(help_text="the other package, normalised")
    version = serializers.CharField(help_text="its current version")
    classes = serializers.ListField(
        child=serializers.CharField(), help_text="classes both define"
    )
    methods = ConflictingMethodSerializer(
        many=True, help_text="methods extensions of both add"
    )


class ConflictsSerializer(serializers.Serializer):
    package = serializers.CharField()
    version = serializers.CharField(
        allow_null=True, help_text="the current version, null if there is none"
    )
    conflicts = PackageConflictsSerializer(many=True)


class MethodSerializer(serializers.Serializer):
    name = serializers.CharField()
    class_method = serializers.BooleanField()


class CheckedClassSerializer(serializers.Serializer):
    name = serializers.CharField()
    superclass = serializers.CharField(help_text='"" for extensions and Object')
    extension = serializers.BooleanField()
    path = serializers.CharField()
    line = serializers.IntegerField()
    platform = serializers.CharField(help_text='"osx", "linux", … or ""')
    methods = MethodSerializer(many=True)


class FileErrorSerializer(serializers.Serializer):
    path = serializers.CharField()
    message = serializers.CharField()


class CheckSerializer(serializers.Serializer):
    classes = CheckedClassSerializer(many=True)
    conflicts = PackageConflictsSerializer(many=True)


# `errors` would replace the serializer's own `errors`
CheckSerializer._declared_fields["errors"] = FileErrorSerializer(
    many=True, help_text=".sc files that don't parse"
)


def class_hit(definition: ClassDefinition) -> dict:
    return {
        "name": definition.name,
        "extension": definition.is_extension,
        "package": definition.release.project.normalized_name,
        "version": definition.release.version,
        "path": definition.path,
    }


def method_hit(method: MethodDefinition) -> dict:
    definition = method.class_definition
    return {
        **class_hit(definition),
        "name": method.name,
        "class": definition.name,
        "class_method": method.is_class_method,
    }


def conflicting_method(conflict: NameConflict) -> dict:
    return {
        "class": conflict.class_name,
        "name": conflict.method,
        "class_method": conflict.is_class_method,
    }


def package_conflicts(groups: list[PackageConflicts]) -> list[dict]:
    return [
        {
            "package": group.other.project.normalized_name,
            "version": group.other.version,
            "classes": [conflict.class_name for conflict in group.classes],
            "methods": [conflicting_method(conflict) for conflict in group.methods],
        }
        for group in groups
    ]


class PublicApiView(APIView):
    authentication_classes: list = []
    permission_classes = [AllowAny]


class ClassSearchView(PublicApiView):
    @extend_schema(
        parameters=[
            OpenApiParameter("q", required=True, description="the start of a name")
        ],
        responses=SearchSerializer,
        summary="Search classes and methods",
        description="Classes, class extensions and methods of each package's "
        "newest final, non-yanked version whose name starts with `q`, ignoring "
        "the case, exact matches first. At most 50 of each.",
    )
    def get(self, request):
        results = search(request.query_params.get("q", ""))
        return Response(
            {
                "classes": [class_hit(c) for c in results.classes],
                "methods": [method_hit(m) for m in results.methods],
                "truncated": results.truncated,
            }
        )


class PackageConflictsView(PublicApiView):
    @extend_schema(
        responses=ConflictsSerializer,
        summary="Naming conflicts of a package",
        description="The clashes of the package's current version (newest "
        "final, non-yanked) with the current versions of the other packages, per "
        "package: classes both define (sclang refuses to compile them together) "
        "and methods extensions of both add (one silently replaces the other). "
        "Files in different platform folders don't clash.",
    )
    def get(self, request, name: str):
        project = get_object_or_404(
            Project.objects.public(), normalized_name=normalize_name(name)
        )
        release = current_release(project)
        groups = conflicts_by_package(release) if release is not None else []
        return Response(
            {
                "package": project.normalized_name,
                "version": release.version if release else None,
                "conflicts": package_conflicts(groups),
            }
        )


class ClassCheckView(APIView):
    authentication_classes = [BearerTokenAuthentication]
    permission_classes = [HasPublishScope]

    @extend_schema(
        request={"application/gzip": OpenApiTypes.BINARY},
        parameters=[
            OpenApiParameter(
                "package",
                description="the package the artifact belongs to: its own "
                "classes don't clash",
            )
        ],
        responses=CheckSerializer,
        summary="Check a quark artifact",
        description="Reads the classes of a quark artifact (the raw `.tar.gz` as "
        "body with a `Content-Length`, like the upload, at most "
        "`MAGNET_MAX_UPLOAD_SIZE`) and returns them with the clashes with the "
        "current versions of the other packages, e.g. before `magnet publish`. "
        "Nothing is stored. Needs a token with the `publish` scope.",
    )
    def post(self, request):
        exclude = (
            Project.objects.filter(
                normalized_name=normalize_name(request.query_params.get("package", ""))
            )
            .values_list("pk", flat=True)
            .first()
        )
        with read_upload(request) as file:
            read_archive(file)
            definitions, errors = read_class_files(file)
        return Response(
            {
                "classes": [checked_class(d) for d in definitions],
                "errors": [{"path": e.path, "message": e.message} for e in errors],
                "conflicts": package_conflicts(
                    group_by_package(find_conflicts(definitions, exclude))
                ),
            }
        )


def checked_class(definition: Definition) -> dict:
    cls, methods = definition
    return {
        "name": cls.name,
        "superclass": cls.superclass,
        "extension": cls.is_extension,
        "path": cls.path,
        "line": cls.line,
        "platform": cls.platform,
        "methods": [
            {"name": m.name, "class_method": m.is_class_method} for m in methods
        ],
    }
