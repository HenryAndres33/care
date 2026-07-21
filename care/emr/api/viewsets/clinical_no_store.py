from django.utils.cache import patch_vary_headers


class ClinicalNoStoreResponseMixin:
    """Prevent authenticated clinical API responses from being cached."""

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "no-store"
        response["Pragma"] = "no-cache"
        patch_vary_headers(response, ("Authorization", "Cookie"))
        return response
