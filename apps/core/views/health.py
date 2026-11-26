from ansible_base.lib.constants import STATUS_DEGRADED, STATUS_GOOD
from ansible_base.lib.utils.views.ansible_base import AnsibleBaseView
from django.db import close_old_connections, connection
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response


class SegmentSendSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=["ok", "failed"], required=False)
    last_failure_at = serializers.CharField(allow_blank=True, required=False)
    last_success_at = serializers.CharField(allow_blank=True, required=False)
    error = serializers.CharField(allow_blank=True, required=False)


class ChecksSerializer(serializers.Serializer):
    database = serializers.CharField(help_text="'ok' or 'error: <message>'")
    segment_send = SegmentSendSerializer(required=False)


class HealthSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=["healthy", "unhealthy"])
    checks = ChecksSerializer()


class HealthView(AnsibleBaseView):
    """
    Health check endpoint to verify service health.

    Checks database connectivity and returns overall health status.
    """

    permission_classes = [AllowAny]
    authentication_classes = []

    @extend_schema(
        summary="Health check endpoint to verify service health",
        description="Checks database connectivity and returns overall health status.",
        responses={
            200: HealthSerializer,
            503: HealthSerializer,
        },
    )
    def get(self, request):
        health_status: dict = {"status": STATUS_GOOD, "checks": {}}

        # Database check
        try:
            # Close any stale connections before health check
            close_old_connections()
            connection.ensure_connection()
            health_status["checks"]["database"] = "ok"
        except Exception as e:
            health_status["status"] = STATUS_DEGRADED
            health_status["checks"]["database"] = f"error: {str(e)}"

        # Segment send check (no data is shown if there's no payloads to check)
        try:
            from apps.tasks.models import AnonymizedMetricsPayload

            # Only fetch the columns needed for the health check; the payload body is large and unused here.
            most_recent = AnonymizedMetricsPayload.objects.only("status", "modified").order_by("-modified").first()
            if most_recent is not None:
                health_status["checks"]["segment_send"] = {}

                if most_recent.status == "failed":
                    health_status["checks"]["segment_send"]["status"] = "failed"
                    health_status["checks"]["segment_send"]["last_failure_at"] = str(most_recent.modified.isoformat())
                else:
                    health_status["checks"]["segment_send"]["status"] = "ok"
                    health_status["checks"]["segment_send"]["last_success_at"] = str(most_recent.modified.isoformat())
        except Exception as e:
            health_status["checks"]["segment_send"] = {}
            health_status["checks"]["segment_send"]["error"] = str(e)

        http_status = (
            status.HTTP_200_OK if health_status["status"] == STATUS_GOOD else status.HTTP_503_SERVICE_UNAVAILABLE
        )

        return Response(health_status, status=http_status)
