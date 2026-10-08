"""The scheduled jobs: what is due, how each last went, and running one now."""
from datetime import date

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from ..audit import audit
from ..exceptions import NotFound
from ..models import JobRun
from ..permissions import IsAdmin
from ..serializers import JobRunSerializer
from ..services import jobs
from .helpers import paginate


@api_view(["GET"])
def overview(request):
    return Response(jobs.overview(date.today()))


@api_view(["GET"])
def runs(request):
    """The history, newest first; ?job= for one."""
    qs = JobRun.objects.select_related("triggered_by")
    if request.query_params.get("job"):
        qs = qs.filter(job=request.query_params["job"])
    return Response(paginate(request, qs, JobRunSerializer, default_size=25))


@api_view(["POST"])
@permission_classes([IsAdmin])
def run_now(request, key: str):
    """Run a job at once, for today, due or not."""
    job = jobs.BY_KEY.get(key)
    if job is None:
        raise NotFound("No such job")
    run = jobs.run_one(job, date.today(), request.user)
    audit(request.user, "run_job", "system", run.id, f"{job.label}: {run.status}")
    return Response(JobRunSerializer(run).data, status=status.HTTP_201_CREATED)
