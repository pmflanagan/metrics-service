from drf_spectacular.utils import extend_schema, extend_schema_view

from apps.core.models import Organization
from apps.core.v1.serializers import OrganizationSerializer

from .base import BaseViewSet


@extend_schema_view(
    list=extend_schema(
        summary="List organizations.",
        description="Returns a list of organizations.",
    ),
)
class OrganizationViewSet(BaseViewSet):
    """Read and update viewset for Organization resources with RBAC filtering.

    Organizations are managed by the AAP resource server (gateway) and synced
    into metrics-service automatically. Creating organizations directly via this
    API is not supported; use the gateway to manage organizations.
    """

    # POST (create) is intentionally excluded: metrics-service is not the provider
    # of organization data (is_provider=False in resource_api.py). Organizations are
    # synced from the AAP gateway via the DAB resource registry. Allowing creation
    # here would trigger a DAB post_save signal that crashes when the resource server
    # is configured (AAP-74775).
    http_method_names = ["get", "put", "patch", "delete", "head", "options", "trace"]

    queryset = Organization.objects.select_related("created_by", "modified_by", "resource").all()
    serializer_class = OrganizationSerializer
