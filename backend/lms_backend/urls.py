"""URL routing.

/api/...     the JSON API consumed by the React app
/admin/      Django admin, handy for poking at SQL Server rows through a UI
/            the built React SPA (frontend/dist), when it has been built
"""
from django.conf import settings
from django.contrib import admin
from django.http import FileResponse, Http404, JsonResponse
from django.urls import include, path, re_path
from django.views.static import serve


def health(_request):
    return JsonResponse({"status": "ok", "app": settings.APP_NAME, "currency": settings.CURRENCY})


def spa(_request, *_args, **_kwargs):
    index = settings.FRONTEND_DIST / "index.html"
    if not index.exists():
        raise Http404(
            "The React build is missing. Run 'npm install && npm run build' in the frontend "
            "directory, or use the Vite dev server on http://localhost:5173."
        )
    return FileResponse(open(index, "rb"), content_type="text/html")


urlpatterns = [
    path("api/health", health, name="health"),
    path("api/", include("core.urls")),
    path("admin/", admin.site.urls),
]

# Serve the SPA and its hashed assets straight from frontend/dist. In a real
# deployment a web server or CDN would do this instead.
if settings.FRONTEND_DIST.exists():
    urlpatterns += [
        re_path(r"^assets/(?P<path>.*)$", serve, {"document_root": settings.FRONTEND_DIST / "assets"}),
        re_path(r"^(?!api/|admin/|static/).*$", spa),
    ]
else:
    urlpatterns += [path("", spa)]
