import asyncio
import logging
from typing import List, Dict, Any, Set
from urllib.parse import urlparse
from datetime import datetime
from playwright.async_api import async_playwright, Browser, BrowserContext, Page
from config_loader import Config
from network_interceptor import NetworkInterceptor
from navigation_handler import NavigationHandler, INTERACTIVE_SELECTORS, POPUP_CONTAINER_SELECTORS

logger = logging.getLogger(__name__)


class APIMapper:
    """Main API mapping engine."""

    def __init__(self, config: Config):
        self.config = config
        self.interceptor = NetworkInterceptor()
        self.navigator = NavigationHandler(config)
        self.playwright = None
        self.browser: Browser = None
        self.context: BrowserContext = None
        self.page: Page = None

    async def initialize(self):
        """Initialize browser and authentication."""
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
            headless=True,
            args=['--disable-blink-features=AutomationControlled']
        )

        # Create context with realistic settings
        self.context = await self.browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            http_credentials=self.config.http_credentials
        )

        self.page = await self.context.new_page()

        # Set up network interception
        await self._setup_network_interception()


    async def _setup_network_interception(self):
        """Set up network request/response interception."""
        pending_requests = {}
        captured_urls = set()  # Track captured URLs to avoid duplicates
        
        # Parse start URL host to identify internal calls
        start_parsed = urlparse(self.config.start_url)
        start_host = start_parsed.netloc

        def is_external_url(url: str) -> bool:
            """Check if URL is external to the webapp."""
            try:
                parsed = urlparse(url)
                return parsed.netloc != start_host
            except Exception:
                return True

        async def handle_response(response):
            """Handle response events - this is where we capture the body."""
            request = response.request
            url_key = request.url
            
            # Skip internal calls
            if not is_external_url(url_key):
                return
            
            # Skip if already captured
            if url_key in captured_urls:
                return
            
            # Get or create request data
            request_data = pending_requests.get(url_key)
            if not request_data:
                # Create new request data if not found
                request_data = await self.interceptor.handle_request(request)
            
            try:
                await self.interceptor.handle_response(request_data, response)
                captured_urls.add(url_key)
            except Exception as e:
                logger.error("Error handling response for %s: %s", url_key, e, exc_info=True)
                # Still mark as captured to avoid retries
                captured_urls.add(url_key)
            
            # Clean up from pending
            pending_requests.pop(url_key, None)

        async def handle_request_failed(request):
            """Handle failed requests (no response received)."""
            url_key = request.url

            # Skip internal calls
            if not is_external_url(url_key):
                return

            # Only capture if we haven't already captured this URL
            if url_key not in captured_urls:
                # Capture requests that failed (e.g., network errors, timeouts, Cloudflare challenges)
                request_data = await self.interceptor.handle_request(request)
                # Mark as failed
                request_data['response'] = {
                    'status': 0,
                    'error': 'Request failed',
                    'timestamp': int(datetime.now().timestamp() * 1000)
                }
                self.interceptor.requests.append(request_data)
                captured_urls.add(url_key)
        
        async def handle_request_finished(request):
            """Handle requests that finished but might not have triggered response event."""
            url_key = request.url

            # Skip internal calls
            if not is_external_url(url_key):
                return

            # Only handle if we haven't already captured this request via handle_response
            if url_key not in captured_urls:
                # Get request data from pending or create new
                request_data = pending_requests.get(url_key)
                if not request_data:
                    request_data = await self.interceptor.handle_request(request)
                
                # Try to get response if available
                try:
                    # request.response is a property, not a method
                    response = request.response
                    if response and hasattr(response, 'status'):
                        # Response is valid, try to process it
                        # But be careful - body might not be available anymore
                        try:
                            await self.interceptor.handle_response(request_data, response)
                            captured_urls.add(url_key)
                        except Exception as body_error:
                            # Body might not be available, but we can still save the request
                            logger.warning("Could not read body for %s: %s", url_key, body_error)
                            request_data['response'] = {
                                'status': response.status if hasattr(response, 'status') else 0,
                                'error': f'Body not available: {str(body_error)}',
                                'timestamp': int(datetime.now().timestamp() * 1000)
                            }
                            self.interceptor.requests.append(request_data)
                            captured_urls.add(url_key)
                    else:
                        # No response available - this might be a failed request
                        request_data['response'] = {
                            'status': 0,
                            'note': 'Request finished but no response available',
                            'timestamp': int(datetime.now().timestamp() * 1000)
                        }
                        self.interceptor.requests.append(request_data)
                        captured_urls.add(url_key)
                except Exception as e:
                    # Response not available or error accessing it
                    request_data['response'] = {
                        'status': 0,
                        'note': f'Request finished but response not accessible: {str(e)}',
                        'timestamp': int(datetime.now().timestamp() * 1000)
                    }
                    self.interceptor.requests.append(request_data)
                    captured_urls.add(url_key)

        async def handle_request(request):
            """Handle request events - store request data for later."""
            url_key = request.url
            
            # Skip internal calls
            if not is_external_url(url_key):
                return

            # logger.debug("Intercepted request %s (type: %s)", url_key, request.resource_type)

            # Store request data for all requests 
            if url_key not in pending_requests:
                request_data = await self.interceptor.handle_request(request)
                pending_requests[url_key] = request_data
        
        def setup_page(target_page):
            # Set up request handler - this captures all requests
            target_page.on('request', handle_request)
            
            # Set up response handler
            target_page.on('response', handle_response)
            
            # Set up failed request handler - capture requests that don't get a response
            target_page.on('requestfailed', handle_request_failed)
            
            # Set up finished request handler - capture requests that finished
            target_page.on('requestfinished', handle_request_finished)
            
        # Apply to main page
        setup_page(self.page)
        
        # Apply to any newly created pages (e.g. target="_blank" links)
        self.context.on('page', setup_page)

    async def map_website(self) -> Dict[str, Any]:
        """Main mapping function."""
        logger.info("Starting API mapping for: %s", self.config.start_url)

        # Navigate to start URL
        if not await self.navigator.navigate_to(self.page, self.config.start_url, 0):
            logger.error("Failed to navigate to start URL")
            return {"api_calls": []}

        self.interceptor.set_context(self.config.start_url, 0)

        # Main navigation loop
        await self._explore_page(self.page, 0)

        # Get all api calls from interceptor
        api_calls = self._extract_relevant_data_from_requests()

        logger.info("Mapping complete. Found %d unique api calls.", len(api_calls))

        return {"api_calls": api_calls}

    async def _explore_page(self, page: Page, depth: int):
        """Explore a single page by clicking elements."""
        if depth >= self.config.max_depth:
            return

        base_url = page.url
        logger.info("Exploring page at depth %d: %s", depth, base_url)

        # Fill forms first to enable submit buttons
        await self.navigator.fill_page_forms(page)

        # Get clickable elements
        clickable_elements = await self.navigator.get_clickable_elements(page)
        logger.info("Found %d clickable elements", len(clickable_elements))

        # Click elements and explore
        for i, element in enumerate(clickable_elements):
            if self.navigator.clicks_on_current_page >= self.config.max_clicks_per_page:
                break
                
            # Verify we are still on the base page before clicking
            if page.url != base_url:
                logger.warning("Restoring state: Expected %s, got %s", base_url, page.url)
                try:
                    await page.goto(base_url, wait_until='networkidle')
                except Exception as e:
                    logger.error("Failed to restore state to %s: %s", base_url, e)
                    continue

            # Get element text for logging
            element_text = ""
            try:
                element_text = (await element.text_content() or "").strip()
                if not element_text:
                    element_text = (await element.get_attribute('aria-label') or "").strip()
                if not element_text:
                    element_text = (await element.get_attribute('title') or "").strip()
            except Exception:
                pass
            label = f" ('{element_text[:30]}')" if element_text else ""
            logger.debug("Clicking element %d/%d of depth %d%s", i+1, len(clickable_elements), depth, label)

            # Set interceptor context
            self.interceptor.set_context(page.url, depth)

            # Click and wait
            clicked = await self.navigator.click_element(page, element)
            if clicked:
                # Wait for network activity
                await page.wait_for_timeout(self.config.network_idle_timeout)

                # Check if we navigated to a new page
                current_url = page.url
                
                # Use base_url to detect navigation
                if current_url == base_url:
                    # No navigation — check if new interactive elements appeared (e.g. menu/dropdown)
                    await self._interact_with_new_elements(page, depth)
                    # Re-check URL since popup interactions may have navigated
                    current_url = page.url

                if current_url != base_url:
                    # Check if we should follow this URL
                    should_follow = self.navigator._should_follow_url(current_url)
                    
                    if should_follow and current_url not in self.navigator.visited_urls:
                        # Check DOM hash to skip semantically duplicate pages
                        dom_hash = await self.navigator._get_dom_hash(page)
                        if dom_hash and dom_hash in self.navigator.visited_dom_hashes:
                            logger.debug("Skipping duplicate page (DOM hash match): %s", current_url)
                        else:
                            if dom_hash:
                                self.navigator.visited_dom_hashes.add(dom_hash)
                            # New page, explore it recursively
                            self.navigator.visited_urls.add(current_url)
                            # Save parent page's click counter before exploring deeper page
                            saved_clicks = self.navigator.clicks_on_current_page
                            self.navigator.clicks_on_current_page = 0
                            await self._explore_page(page, depth + 1)
                            # Restore parent page's click counter
                            self.navigator.clicks_on_current_page = saved_clicks
                    elif not should_follow:
                        logger.debug("Skipping external/excluded URL: %s", current_url)
                    
                    # Always try to go back if we navigated away
                    try:
                        # Try to go back with timeout
                        await page.go_back(
                            wait_until='load',
                            timeout=self.config.wait_timeout
                        )
                        await page.wait_for_timeout(1000)
                    except Exception as e:
                        logger.warning("Could not go back: %s", e)
                        # If go back fails, try explicit goto
                        try:
                            if page.url != base_url:
                                await page.goto(base_url, wait_until='networkidle')
                        except Exception:
                            pass

        # Also try to follow links on the page
        await self._follow_links_on_page(page, depth)

    async def _interact_with_new_elements(self, page: Page, depth: int):
        """After a non-navigating click, check for newly appeared elements (menus, dropdowns) and interact with them."""
        # Dismiss any calendar overlay that may have appeared
        if await self.navigator._dismiss_calendar_overlay(page):
            logger.debug("Dismissed calendar overlay after click")
            return

        base_url = page.url
        popup_selectors = POPUP_CONTAINER_SELECTORS

        for selector in popup_selectors:
            try:
                containers = await page.query_selector_all(selector)
                for container in containers:
                    if not await container.is_visible():
                        continue

                    interactive = await container.query_selector_all(INTERACTIVE_SELECTORS)
                    if not interactive:
                        continue

                    overlay_hash = await self.navigator._get_overlay_hash(container)
                    if overlay_hash in self.navigator.visited_overlay_hashes:
                        logger.debug("Skipping already-seen overlay (hash: %s)", overlay_hash[:8])
                        continue
                    self.navigator.visited_overlay_hashes.add(overlay_hash)

                    # Fill any forms inside the popup before clicking its elements
                    await self.navigator.fill_page_forms(page, root=container)

                    logger.debug("Found popup/menu with %d interactive elements", len(interactive))
                    for el in interactive:
                        try:
                            if not await el.is_visible():
                                continue
                            if await self.navigator._is_destructive_action(el):
                                continue

                            label = ''
                            try:
                                label = (await el.text_content() or '').strip()
                            except Exception:
                                pass
                            logger.debug("  Clicking popup element: '%s'", label[:30])

                            self.interceptor.set_context(page.url, depth)
                            await el.click(timeout=3000)
                            await page.wait_for_timeout(self.config.network_idle_timeout)

                            current_url = page.url
                            if current_url != base_url:
                                should_follow = self.navigator._should_follow_url(current_url)
                                if should_follow and current_url not in self.navigator.visited_urls:
                                    self.navigator.visited_urls.add(current_url)
                                    saved_clicks = self.navigator.clicks_on_current_page
                                    self.navigator.clicks_on_current_page = 0
                                    await self._explore_page(page, depth + 1)
                                    self.navigator.clicks_on_current_page = saved_clicks
                                try:
                                    await page.go_back(wait_until='load', timeout=self.config.wait_timeout)
                                    await page.wait_for_timeout(1000)
                                except Exception:
                                    try:
                                        if page.url != base_url:
                                            await page.goto(base_url, wait_until='networkidle')
                                    except Exception:
                                        pass
                        except Exception as e:
                            logger.warning("  Could not click popup element: %s", e)
                            continue
                    return  # found and processed a popup, done
            except Exception:
                continue

    async def _follow_links_on_page(self, page: Page, depth: int):
        """Follow links on current page."""
        if depth >= self.config.max_depth:
            return

        try:
            links = await page.query_selector_all('a[href]')
            for link in links[:10]:  # Limit to first 10 links
                try:
                    href = await link.get_attribute('href')
                    if href and href not in self.navigator.visited_urls:
                        if self.navigator._should_follow_url(href):
                            # Set context
                            self.interceptor.set_context(page.url, depth)
                            
                            # Navigate
                            if await self.navigator.navigate_to(page, href, depth + 1):
                                saved_clicks = self.navigator.clicks_on_current_page
                                self.navigator.clicks_on_current_page = 0
                                await self._explore_page(page, depth + 1)
                                self.navigator.clicks_on_current_page = saved_clicks
                                # Try to go back if we navigated to a new page
                                if depth > 0:
                                    try:
                                        await page.go_back(
                                            wait_until='load',
                                            timeout=self.config.wait_timeout
                                        )
                                        await page.wait_for_timeout(1000)
                                    except Exception as e:
                                        logger.warning("Could not go back from %s: %s", href, e)
                                        # Continue without going back
                                        pass
                except Exception as e:
                    logger.error("Error processing link: %s", e)
                    continue
        except Exception as e:
            logger.error("Error following links: %s", e)

    def _extract_relevant_data_from_requests(self) -> List[Dict[str, Any]]:
        """Extract relevant data from captured requests."""
        requests = self.interceptor.get_requests()
        
        # Group by host
        result_map = {}
        for req in requests:
            parsed_url = urlparse(req['url'])
            host = parsed_url.netloc
            if not host:
                continue
                
            key = host
            current_auth = req.get('authentication', 'None')
            
            if key not in result_map:
                result_map[key] = {
                    'host': host,
                    'authentication': current_auth,
                }
            
            # Update auth if we found something more specific than what we had
            existing_auth = result_map[key]['authentication']
            if existing_auth in ['None', 'anonymous'] and current_auth not in ['None', 'anonymous']:
                result_map[key]['authentication'] = current_auth
            
            # Also if we have a "Required: ..." which is better than "None" but maybe we want 
            # to capture actual auth if we eventually got it? 
            # Priority: Actual Auth > Required Auth > None
            if "Required" in existing_auth and "OAuth" in current_auth: # e.g. we saw challenge first, then successful auth
                 result_map[key]['authentication'] = current_auth

        return list(result_map.values())

    async def cleanup(self):
        """Clean up resources."""
        if self.page:
            await self.page.close()
        if self.context:
            await self.context.close()
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()

