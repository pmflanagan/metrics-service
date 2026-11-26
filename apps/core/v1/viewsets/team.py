from drf_spectacular.utils import extend_schema, extend_schema_view

from apps.core.models import Team
from apps.core.v1.serializers import TeamSerializer

from .base import BaseViewSet


@extend_schema_view(
    list=extend_schema(
        summary="List teams.",
        description="Returns a list of teams.",
    ),
    retrieve=extend_schema(
        summary="Get specific teams by ID.",
        description="Returns a specific teams by ID.",
    ),
    create=extend_schema(
        summary="Create a team.",
        description="Create a new team object.",
        request=TeamSerializer,
    ),
    update=extend_schema(
        summary="Update a specific team by ID.",
        description="Update a specific team by ID.",
    ),
    partial_update=extend_schema(
        summary="Partially update a specific team by ID.",
        description="Partially update a specific team by ID.",
    ),
    destroy=extend_schema(
        summary="Delete a specific team by ID.",
        description="Delete a specific team record by ID.",
    ),
)
class TeamViewSet(BaseViewSet):
    """CRUD viewset for Team resources with RBAC filtering."""

    queryset = Team.objects.select_related("organization", "created_by", "modified_by", "resource").all()
    serializer_class = TeamSerializer
