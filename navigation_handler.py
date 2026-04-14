"""Handle smart navigation through website.

This module composes ElementClassifier, FormFiller, OverlayHandler, and DOMHasher
to provide a unified navigation interface. Delegation methods preserve backward
compatibility with existing callers and tests.
"""
from typing import List, Set
from playwright.async_api import Page, Locator
from urllib.parse import urlparse
from config_loader import Config
from css_selectors import (
    CLICKABLE_SELECTORS,
    MODAL_CONTAINER_SELECTORS,
    INTERACTIVE_SELECTORS,
    POPUP_CONTAINER_SELECTORS,
)
from element_classifier import ElementClassifier
from dom_hasher import DOMHasher
from form_filler import FormFiller
from overlay_handler import OverlayHandler
import logging
import re

logger = logging.getLogger(__name__)


class NavigationHandler:
    """Handle smart navigation through website."""

    def __init__(self, config: Config, dom_hasher: DOMHasher = None):
        self.config = config
        self.visited_urls: Set[str] = set()
        self.current_depth = 0
        self.clicks_on_current_page = 0

        # Composed modules
        self.classifier = ElementClassifier(config)
        self.dom_hasher = dom_hasher or DOMHasher()
        self.form_filler = FormFiller(config)
        self.overlay_handler = OverlayHandler(self.classifier, self.form_filler)

    # --- Backward-compat proxies for dom_hasher state ---

    @property
    def visited_dom_hashes(self) -> Set[str]:
        return self.dom_hasher.visited_dom_hashes

    @visited_dom_hashes.setter
    def visited_dom_hashes(self, value: Set[str]):
        self.dom_hasher.visited_dom_hashes = value

    @property
    def visited_overlay_hashes(self) -> Set[str]:
        return self.dom_hasher.visited_overlay_hashes

    @visited_overlay_hashes.setter
    def visited_overlay_hashes(self, value: Set[str]):
        self.dom_hasher.visited_overlay_hashes = value

    # --- URL filtering ---

    def _should_follow_url(self, url: str) -> bool:
        """Determine if URL should be followed."""
        if not url or url.startswith('javascript:') or url.startswith('mailto:'):
            return False

        try:
            parsed_url = urlparse(url)
            start_parsed = urlparse(self.config.start_url)
            is_same_domain = (parsed_url.scheme == start_parsed.scheme and
                            parsed_url.netloc == start_parsed.netloc)
            return is_same_domain
        except Exception:
            return False

    # --- Element interaction ---

    async def get_clickable_elements(self, page: Page) -> List[Locator]:
        """Get all clickable elements on current page."""
        all_elements = []
        seen_elements = set()

        for selector in CLICKABLE_SELECTORS:
            try:
                page_elements = await page.query_selector_all(selector)
                for idx, elem in enumerate(page_elements):
                    try:
                        locator = page.locator(selector).nth(idx)

                        try:
                            elem_html = await elem.evaluate('el => el.outerHTML')
                            if elem_html in seen_elements:
                                continue
                            seen_elements.add(elem_html)
                        except Exception:
                            pass

                        try:
                            if await locator.is_visible():
                                is_enabled = await elem.evaluate('''el => {
                                    if (el.disabled) return false;
                                    if (el.getAttribute('aria-disabled') === 'true') return false;
                                    const style = window.getComputedStyle(el);
                                    if (style.pointerEvents === 'none') return false;
                                    return true;
                                }''')

                                if is_enabled and not await self.classifier.is_destructive_action(locator):
                                    if await self.classifier.is_date_picker_element(elem):
                                        logger.debug("Skipping date picker element")
                                        continue
                                    all_elements.append(locator)
                                    if len(all_elements) >= self.config.max_clicks_per_page:
                                        return all_elements
                        except Exception:
                            continue
                    except Exception:
                        continue
            except Exception:
                continue

        return all_elements[:self.config.max_clicks_per_page]

    async def navigate_to(self, page: Page, url: str, depth: int = 0) -> bool:
        """Navigate to URL and check if should continue."""
        if depth > self.config.max_depth:
            return False

        if url in self.visited_urls:
            return False

        if not self._should_follow_url(url):
            return False

        try:
            self.current_depth = depth
            self.clicks_on_current_page = 0
            self.visited_urls.add(url)

            await page.goto(url, wait_until='networkidle', timeout=self.config.wait_timeout)
            await page.wait_for_timeout(self.config.network_idle_timeout)
            return True

        except Exception as e:
            logger.error("Navigation error to %s: %s", url, e)
            return False

    async def click_element(self, page: Page, element: Locator) -> bool:
        """Click element and handle navigation."""
        if self.clicks_on_current_page >= self.config.max_clicks_per_page:
            return False

        try:
            if not await element.is_visible():
                logger.debug("Element is not visible, skipping click.")
                return False

            try:
                await element.scroll_into_view_if_needed(timeout=5000)
                await page.wait_for_timeout(500)
            except Exception as scroll_err:
                logger.debug("Scroll failed: %s. Attempting click without scroll.", scroll_err)

            url_before = page.url

            try:
                await element.click(timeout=5000)
            except Exception as click_err:
                error_msg = str(click_err)
                if 'intercepts pointer events' in error_msg:
                    interceptor_match = re.search(r'<(\w+)\b', error_msg.split('intercepts pointer events')[0].rsplit('\n', 1)[-1])
                    interceptor_tag = interceptor_match.group(1).lower() if interceptor_match else ''

                    if interceptor_tag in ('html', 'body'):
                        logger.info("Click intercepted by <%s>, not a modal. Force-clicking.", interceptor_tag)
                        await element.click(timeout=3000, force=True)
                    else:
                        has_modal = False
                        for selector in MODAL_CONTAINER_SELECTORS:
                            try:
                                els = await page.query_selector_all(selector)
                                for el in els:
                                    if await el.is_visible():
                                        has_modal = True
                                        break
                                if has_modal:
                                    break
                            except Exception:
                                continue

                        if has_modal:
                            logger.warning("Element click intercepted by a modal. Attempting to interact with overlay...")
                            await self.overlay_handler.handle_overlay(page)
                            try:
                                await element.click(timeout=3000)
                            except Exception:
                                await element.click(timeout=3000, force=True)
                        else:
                            logger.info("Click intercepted but no modal detected. Force-clicking.")
                            await element.click(timeout=3000, force=True)
                else:
                    try:
                        await element.click(timeout=3000, force=True)
                    except Exception:
                        raise click_err

            self.clicks_on_current_page += 1

            try:
                await page.wait_for_load_state('networkidle', timeout=5000)
            except Exception:
                await page.wait_for_timeout(1000)

            url_after = page.url
            if url_before == url_after:
                await page.wait_for_timeout(300)

            return True

        except Exception as e:
            logger.error("Click error: %s", e)
            return False

    # --- State management ---

    def reset_page_counters(self):
        """Reset counters for new page."""
        self.clicks_on_current_page = 0

    def can_continue_navigation(self) -> bool:
        """Check if navigation can continue."""
        return self.current_depth < self.config.max_depth

    # --- Delegation methods for backward compatibility ---

    async def _is_destructive_action(self, element, text: str = "") -> bool:
        return await self.classifier.is_destructive_action(element, text)

    async def _is_date_picker_element(self, element) -> bool:
        return await self.classifier.is_date_picker_element(element)

    async def _is_calendar_overlay(self, container) -> bool:
        return await self.classifier.is_calendar_overlay(container)

    async def _get_dom_hash(self, page: Page) -> str:
        return await self.dom_hasher.get_dom_hash(page)

    async def _get_overlay_hash(self, container) -> str:
        return await self.dom_hasher.get_overlay_hash(container)

    async def _dismiss_calendar_overlay(self, page: Page) -> bool:
        return await self.overlay_handler.dismiss_calendar_overlay(page)

    async def _handle_overlay(self, page: Page):
        return await self.overlay_handler.handle_overlay(page)

    async def fill_page_forms(self, page: Page, root=None):
        return await self.form_filler.fill_page_forms(page, root)

    async def _generate_value_with_length(self, base_value: str, min_length: int) -> str:
        return await self.form_filler._generate_value_with_length(base_value, min_length)
