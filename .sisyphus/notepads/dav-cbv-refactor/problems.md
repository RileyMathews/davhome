# Problems

## Anti-Patterns: Django CBV Non-Standard Verb Handling

### 1. Missing csrf_exempt for DAV methods
**Risk**: HIGH - DAV clients (iCal, Lightning, iOS Calendar) don't send CSRF tokens
**Anti-pattern**: Using standard Django View without csrf_exempt
```python
# BAD - Will return 403 CSRF failure for PROPFIND/PUT
class MyDavView(View):
    http_method_names = ['get', 'propfind', 'put']
    def propfind(self, request):
        ...
```
**Mitigation**: Apply `@csrf_exempt` to dispatch or class

### 2. Case-sensitive http_method_names
**Risk**: HIGH - Django dispatches to lowercase method names
**Anti-pattern**:
```python
# BAD - Django will not find propfind() method
http_method_names = ['GET', 'PROPFIND', 'PUT']
```
**Mitigation**: Always use lowercase: `['get', 'propfind', 'put']`

### 3. Forgetting Allow header in OPTIONS
**Risk**: MEDIUM - DAV clients query OPTIONS to discover capabilities
**Anti-pattern**:
```python
# BAD - No Allow header returned
def options(self, request):
    return HttpResponse()  # Missing Allow header
```
**Mitigation**: Override options() to include Allow header with uppercase methods

### 4. Missing DAV header advertising
**Risk**: MEDIUM - CalDAV clients check for DAV header
**Anti-pattern**:
```python
# BAD - Client may not recognize as CalDAV server
def dispatch(self, request, ...):
    response = super().dispatch(request, ...)
    # No DAV header added
    return response
```
**Mitigation**: Add `response['DAV'] = '1, calendar-access'` after super().dispatch()

### 5. Not handling 405 Method Not Allowed
**Risk**: MEDIUM - DAV protocol expects 405 with Allow header
**Anti-pattern**:
```python
# BAD - Django default returns text/plain, not DAV-compliant
def dispatch(self, request, ...):
    handler = getattr(self, request.method.lower(), None)
    if handler is None:
        return HttpResponseNotAllowed()  # Missing Allow header
```
**Mitigation**: Return HttpResponseNotAllowed with proper Allow header

### 6. Incorrect Multi-Status response
**Risk**: HIGH - PROPFIND/PROPPATCH require 207 with reason_phrase
**Anti-pattern**:
```python
# BAD - Missing reason_phrase
response = HttpResponse(..., status=207)
# Some clients reject this
```
**Mitigation**: Set `response.reason_phrase = 'Multi-Status'`

### 7. Auth after dispatch vs before
**Risk**: HIGH - Can cause 403 before method is even looked up
**Anti-pattern**:
```python
# BAD - Auth check in each method, not dispatch
def propfind(self, request):
    if not request.user.is_authenticated:  # Too late!
        return HttpResponse(status=401)
    ...
```
**Mitigation**: Handle authentication in dispatch() before calling super()

### 8. Not parsing HTTP_DEPTH header
**Risk**: MEDIUM - PROPFIND clients send Depth: 0/1/infinity
**Anti-pattern**:
```python
# BAD - Ignoring Depth header
def propfind(self, request):
    # Only handles depth=0
    ...
```
**Mitigation**: Parse `request.META.get('HTTP_DEPTH', 'infinity')` and handle accordingly

### 9. Missing XML content-type on DAV responses
**Risk**: LOW - But some strict clients may reject
**Anti-pattern**:
```python
# BAD - Defaulting to text/html
response = HttpResponse(etree.tostring(doc))
# May be rejected by DAV clients
```
**Mitigation**: Set `content_type='text/xml; charset=utf-8'`

### 10. Using function-based view patterns in CBV
**Risk**: MEDIUM - Some patterns from FBVs don't transfer
**Anti-pattern**:
```python
# BAD - Trying to use @require_http_methods decorator
@require_http_methods(["PROPFIND"])
class MyDavView(View):  # Decorator doesn't work on classes
    ...
```
**Mitigation**: Use http_method_names attribute instead

## Summary Table

| Anti-Pattern | Risk Level | Mitigation |
|--------------|------------|------------|
| Missing csrf_exempt | HIGH | @csrf_exempt on dispatch |
| Case-sensitive method names | HIGH | Use lowercase |
| Missing Allow header | MEDIUM | Override options() |
| Missing DAV header | MEDIUM | Add in dispatch |
| No 405 handling | MEDIUM | Return proper 405 |
| Incorrect 207 response | HIGH | Set reason_phrase |
| Auth in methods | HIGH | Auth in dispatch |
| No Depth handling | MEDIUM | Parse HTTP_DEPTH |
| Missing XML content-type | LOW | Set explicitly |
| FBV patterns in CBV | MEDIUM | Use http_method_names |
