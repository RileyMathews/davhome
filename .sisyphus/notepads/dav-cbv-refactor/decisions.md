# Decisions

## Django CBV Dispatch Pattern for Non-Standard HTTP Methods (2026-02-20)

### Authoritative References

1. **Django 6.0 - View.dispatch documentation**
   - URL: https://docs.djangoproject.com/en/6.0/ref/class-based-views/base/
   - Key: Explains dispatch method lookup and http_method_not_allowed flow

2. **Django 6.0 - Supporting other HTTP methods**
   - URL: https://docs.djangoproject.com/en/6.0/topics/class-based-views/#supporting-other-http-methods
   - Key: Official example showing how to add custom method handlers

3. **Django 6.0 - HttpResponseNotAllowed**
   - URL: https://docs.djangoproject.com/en/6.0/ref/request-response
   - Key: Constructor takes list of permitted methods

4. **Django Source - View.dispatch implementation**
   - URL: https://github.com/django/django/blob/cb24bebfab08f55b05599ea1bdcdc159f071225c/django/views/generic/base.py#L134-L143
   - Evidence:
     ```python
     def dispatch(self, request, *args, **kwargs):
         method = request.method.lower()  # Converts to lowercase!
         if method in self.http_method_names:
             handler = getattr(self, method, self.http_method_not_allowed)
         else:
             handler = self.http_method_not_allowed
         return handler(request, *args, **kwargs)
     ```

5. **Django Source - View._allowed_methods implementation**
   - URL: https://github.com/django/django/blob/cb24bebfab08f55b05599ea1bdcdc159f071225c/django/views/generic/base.py#L179-L180
   - Evidence:
     ```python
     def _allowed_methods(self):
         return [m.upper() for m in self.http_method_names if hasattr(self, m)]
     ```
   - Key: Only returns methods that are BOTH in http_method_names AND defined as handlers

### Distilled Pattern for DAV Verbs

```python
from django.views import View
from django.http import HttpResponse

class DavResourceView(View):
    # 1. Extend http_method_names with lowercase DAV verbs
    http_method_names = ["get", "post", "put", "delete", "options",
                          "propfind", "proppatch", "report", "acl",
                          "mkcol", "move", "copy", "lock", "unlock"]
    
    # 2. Define handler methods using LOWERCASE names matching http_method_names
    def propfind(self, request, *args, **kwargs):
        # Handle PROPFIND verb
        return HttpResponse(...)
    
    def proppatch(self, request, *args, **kwargs):
        # Handle PROPPATCH verb  
        return HttpResponse(...)
    
    def report(self, request, *args, **kwargs):
        # Handle REPORT verb
        return HttpResponse(...)
    
    # 3. No need to override options() - Django auto-generates Allow header
    # 4. No need to override http_method_not_allowed() - returns 405 with Allow header
```

### Key Implementation Rules

1. **Handler methods MUST be lowercase** - Django dispatches using `request.method.lower()`, so `propfind()` handles PROPFIND, not `PROPFIND()`

2. **http_method_names MUST contain lowercase entries** - The dispatch checks `method in self.http_method_names` where method is already lowercased

3. **Allow header auto-generated correctly** - The built-in `options()` method uses `_allowed_methods()` which returns UPPERCASE names, BUT only for handlers that actually exist on the class

4. **Handler must exist for Allow header** - If you add "propfind" to http_method_names but don't define `propfind()`, it won't appear in the Allow header

5. **No need to customize http_method_not_allowed** - Default returns `HttpResponseNotAllowed` with proper Allow header listing all supported methods

## Task 3 Foundation Decisions (2026-02-20)

- Added a dedicated CBV foundation package at `dav/cbv/` with `DavView` in `dav/cbv/base.py` and composable mixins in `dav/cbv/mixins.py`.
- `DavView.http_method_names` explicitly includes existing DAV verbs used by current FBVs: `propfind`, `proppatch`, `report`, `mkcalendar`, `mkcol`, `copy`, `move`.
- Applied CSRF exemption at class level with `@method_decorator(csrf_exempt, name="dispatch")` on `DavView` to preserve endpoint parity with current `@csrf_exempt` FBVs.
- Centralized auth-challenge integration via `DavAuthMixin.authenticate_dav_request()`, which delegates to `dav.views_common._require_dav_user` and stores resolved user on `self.dav_user`.
- Centralized DAV header behavior via `DavHeaderMixin.apply_dav_headers()` and `DavView.dispatch()`, so both normal and auth-challenge responses pass through `_dav_common_headers`.
- Implemented explicit `options()` scaffolding in `DavOptionsMixin` returning `204` + `Allow` header, with opt-in per-endpoint method ordering via `allowed_methods` override.
- Overrode `DavView.http_method_not_allowed()` to route 405 behavior through existing `_not_allowed()` helper, preserving DAV audit logging and shared not-allowed response shape.
