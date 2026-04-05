"""Network request/response interceptor for Playwright."""
from typing import Dict, Any, List, Optional
from playwright.async_api import Request, Response, Route
from datetime import datetime
import json
import re
from urllib.parse import urlparse, parse_qs


class NetworkInterceptor:
    """Intercept and store network requests/responses."""

    def __init__(self):
        self.requests: List[Dict[str, Any]] = []
        self.source_url: str = ""
        self.navigation_depth: int = 0

    def set_context(self, source_url: str, navigation_depth: int = 0):
        """Set context for captured requests."""
        self.source_url = source_url
        self.navigation_depth = navigation_depth

    async def handle_request(self, request: Request) -> Dict[str, Any]:
        """Handle request interception."""
        request_data = {
            'url': request.url,
            'method': request.method,
            'headers': request.headers,
            'post_data': await self._get_post_data(request),
            'resource_type': request.resource_type,
            'timestamp': int(datetime.now().timestamp() * 1000),
            'source_url': self.source_url,
            'navigation_depth': self.navigation_depth,
            'authentication': self._detect_authentication(request.headers, request.url)
        }
        return request_data

    def _detect_authentication(self, headers: Dict[str, str], url: str) -> str:
        """
        Detect the authentication method used in the request.
        Returns a string describing the authentication method.
        """
        # 1. Check Authorization header
        # Headers are usually lower-cased by Playwright/browsers, but let's be safe
        auth_header = None
        for k, v in headers.items():
            if k.lower() == 'authorization':
                auth_header = v
                break
        
        if auth_header:
            if auth_header.startswith('Bearer '):
                return "OAuth (Bearer)"
            
            if auth_header.startswith('Basic '):
                return "Basic Auth"
            
            if auth_header.startswith('Negotiate '):
                token = auth_header[10:].strip()
                # NTLM tokens (NTLMSSP...) base64 encode to "TlR..."
                if token.startswith('TlR'):
                    return "NTLM (Negotiate)"
                # Kerberos tickets often start with YII... (GSS-API)
                if token.startswith('YII'):
                    return "Kerberos (Negotiate)"
                return "Negotiate (Unknown)"
            
            if auth_header.startswith('NTLM '):
                return "NTLM"
            
            if auth_header.startswith('Kerberos '):
                return "Kerberos"
                
            return f"Unknown Authorization ({auth_header.split(' ')[0]})"

        # 2. Check common API Key headers
        api_key_headers = [
            'x-api-key', 'x-auth-token', 'x-auth', 'api-key', 'apikey', 'auth-token'
        ]
        for k in headers:
            if k.lower() in api_key_headers:
                return f"API Key ({k})"

        # 3. Check Query Parameters for API Keys
        parsed_url = urlparse(url)
        query_params = parse_qs(parsed_url.query)
        api_key_params = ['api_key', 'apikey', 'key', 'auth_token', 'token']
        
        for param in api_key_params:
            if param in query_params:
                return f"API Key (Query Param: {param})"

        # 4. Check Cookie presence (fallback, as many sites use cookies)
        # Only count it as auth if we didn't find anything stronger
        if 'cookie' in headers or 'Cookie' in headers:
            # Maybe refine this: checking if specific auth cookies exist is hard generic
            # but usually the presence of a cookie header implies session-based auth
            return "Cookie / Session"

        return "None"

    async def handle_response(self, request_data: Dict[str, Any], response: Optional[Response] = None) -> Dict[str, Any]:
        """Handle response and complete request data."""
        try:
            # Check if response is valid and accessible
            if response is None:
                raise ValueError("Response is None")
            
            # Check if response has status attribute (it's a Response object, not a coroutine)
            if not hasattr(response, 'status'):
                raise ValueError(f"Response object is invalid: {type(response)}")
            
            # Safely get status and headers
            status = response.status if hasattr(response, 'status') else 0
            try:
                headers = response.headers
                # Check if headers is a coroutine
                if hasattr(headers, '__await__'):
                    headers = await headers
            except Exception:
                headers = {}
           
            response_data = {
                'status': status,
                'headers': headers,
                'timestamp': int(datetime.now().timestamp() * 1000)
            }

            # --- Auth Fingerprinting (Scenario A) ---
            # 1. 401 Unauthorized - Parse WWW-Authenticate
            if status == 401:
                auth_challenge = self._get_header_value(headers, 'WWW-Authenticate')
                if auth_challenge:
                    response_data['auth_challenge'] = auth_challenge
                    # Update authentication field if it was unknows
                    if request_data.get('authentication', 'None') in ['None', 'anonymous']:
                        if auth_challenge.lower().startswith('basic'):
                            request_data['authentication'] = f"Required: Basic ({auth_challenge})"
                        elif auth_challenge.lower().startswith('bearer'):
                            request_data['authentication'] = f"Required: OAuth/Bearer ({auth_challenge})"
                        elif auth_challenge.lower().startswith('negotiate'):
                            request_data['authentication'] = f"Required: Negotiate ({auth_challenge})"
                        else:
                            request_data['authentication'] = f"Required: {auth_challenge}"

            # 2. Redirects (3xx) - Check for IdP
            if status in [301, 302, 303, 307, 308]:
                location = self._get_header_value(headers, 'Location')
                if location:
                    idp = self._detect_idp_redirect(location)
                    if idp:
                        response_data['idp_redirect'] = idp
                        request_data['authentication'] = f"IdP Redirect: {idp}"
            # ----------------------------------------

            request_data['response'] = response_data
            self.requests.append(request_data)
            return request_data
        except Exception as e:
            print(f"Error handling response for {request_data.get('url')}: {e}")
            import traceback
            traceback.print_exc()
            # Safely get status if response exists and is valid
            status = 0
            if response and hasattr(response, 'status'):
                try:
                    status = response.status
                except Exception:
                    status = 0
            
            request_data['response'] = {
                'status': status,
                'error': str(e),
                'timestamp': int(datetime.now().timestamp() * 1000)
            }
            # Only append if not already in requests (avoid duplicates)
            if request_data not in self.requests:
                self.requests.append(request_data)
            return request_data

    def _get_header_value(self, headers: Dict[str, str], name: str) -> Optional[str]:
        """Case-insensitive header lookup."""
        for k, v in headers.items():
            if k.lower() == name.lower():
                return v
        return None

    def _detect_idp_redirect(self, location: str) -> Optional[str]:
        """Detect if location URL matches known Identity Providers."""
        try:
            parsed = urlparse(location)
            domain = parsed.netloc.lower()
            
            # Common IdP Patterns
            if 'auth0.com' in domain:
                return "Auth0"
            if 'okta.com' in domain or 'oktapreview.com' in domain:
                return "Okta"
            if 'login.microsoftonline.com' in domain:
                return "Azure AD"
            if 'accounts.google.com' in domain:
                return "Google"
            if 'cognito-idp' in domain or 'amazoncognito.com' in domain:
                return "AWS Cognito"
            if 'onelogin.com' in domain:
                return "OneLogin"
            if 'pingidentity.com' in domain:
                return "Ping Identity"
            
            # Check for OAuth2/OIDC params in local redirects
            # e.g., /login?returnTo=... or /oauth/authorize
            if '/oauth' in parsed.path or '/oidc' in parsed.path:
                return "Generic OAuth2/OIDC Endpoint"
            
            return None
        except Exception:
            return None


    async def _get_post_data(self, request: Request) -> Optional[Dict[str, Any]]:
        """Extract POST data from request."""
        if request.method not in ['POST', 'PUT', 'PATCH']:
            return None

        try:
            post_data = request.post_data
            if post_data:
                # Try to parse as JSON
                try:
                    return json.loads(post_data)
                except json.JSONDecodeError:
                    # Try to parse as form data
                    if 'application/x-www-form-urlencoded' in request.headers.get('content-type', ''):
                        from urllib.parse import parse_qs
                        return dict(parse_qs(post_data))
                    return {'raw': post_data}
        except Exception:
            pass

        return None


    def get_requests(self) -> List[Dict[str, Any]]:
        """Get all captured requests."""
        return self.requests.copy()

    def clear(self):
        """Clear captured requests."""
        self.requests.clear()
