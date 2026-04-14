"""Modal and overlay detection, interaction, and dismissal."""
import logging
from playwright.async_api import Page
from element_classifier import ElementClassifier
from form_filler import FormFiller
from css_selectors import (
    CALENDAR_OVERLAY_SELECTORS,
    MODAL_CONTAINER_SELECTORS,
    INTERACTIVE_SELECTORS,
    DISMISS_SELECTORS,
)

logger = logging.getLogger(__name__)


class OverlayHandler:
    """Detect and interact with modals, popups, and calendar overlays."""

    def __init__(self, classifier: ElementClassifier, form_filler: FormFiller):
        self.classifier = classifier
        self.form_filler = form_filler

    async def dismiss_calendar_overlay(self, page: Page) -> bool:
        """Detect and dismiss any visible calendar/datepicker overlay. Returns True if one was dismissed."""
        for selector in CALENDAR_OVERLAY_SELECTORS:
            try:
                elements = await page.query_selector_all(selector)
                for el in elements:
                    try:
                        if not await el.is_visible():
                            continue
                        if not await self.classifier.is_calendar_overlay(el):
                            continue

                        logger.debug("Calendar overlay detected, dismissing...")
                        await page.keyboard.press('Escape')
                        await page.wait_for_timeout(300)

                        # Verify it was dismissed
                        try:
                            if await el.is_visible():
                                # Fallback: click outside the overlay
                                await page.mouse.click(0, 0)
                                await page.wait_for_timeout(300)
                        except Exception:
                            pass  # Element may have been removed from DOM

                        return True
                    except Exception:
                        continue
            except Exception:
                continue
        return False

    async def handle_overlay(self, page: Page):
        """Attempt to interact with and then dismiss any blocking modals."""
        logger.info("Handling overlay: attempting affirmative actions first...")

        # Check if this is a calendar overlay — dismiss immediately without interacting
        if await self.dismiss_calendar_overlay(page):
            logger.info("Dismissed calendar overlay")
            return

        try:
            # 1. Identify active modal container
            modal_container = None

            for selector in MODAL_CONTAINER_SELECTORS:
                try:
                    elements = await page.query_selector_all(selector)
                    for el in elements:
                        if await el.is_visible():
                            modal_container = el
                            break
                    if modal_container:
                        break
                except Exception:
                    continue

            action_taken = False
            if modal_container:
                logger.info("Modal container identified. Filling forms and searching for interactive elements...")
                # Fill forms scoped to the modal container
                await self.form_filler.fill_page_forms(page, root=modal_container)
                try:
                    # Find buttons and links inside the modal
                    interactive_elements = await modal_container.query_selector_all(INTERACTIVE_SELECTORS)

                    for el in interactive_elements:
                        if not await el.is_visible():
                            continue

                        if await self.classifier.is_destructive_action(el):
                            continue

                        combined_text = ''
                        try:
                            combined_text = (await el.text_content() or '').strip()
                        except Exception:
                            pass

                        logger.debug("Clicking actionable element in modal: '%s'", combined_text[:30])
                        try:
                            await el.click(timeout=2000)
                            await page.wait_for_timeout(1000)
                            action_taken = True
                        except Exception as click_err:
                            logger.warning("Could not click modal element: %s", click_err)
                except Exception as e:
                    logger.error("Error exploring modal elements: %s", e)
            else:
                logger.info("Could not explicitly identify modal container. Falling back to targeted selectors.")
                # Fallback to the old method
                action_selectors = [
                    'button:has-text("Confirm")',
                    'button:has-text("Yes")',
                    'button:has-text("Accept")',
                    'button:has-text("Submit")',
                    'button:has-text("Continue")',
                    'button:has-text("Save")',
                    'button:has-text("Create")',
                    'button:has-text("Update")',
                    'button:has-text("Delete")',
                    'input[type="submit"]',
                    '.btn-primary:not([disabled])',
                ]

                for selector in action_selectors:
                    try:
                        elements = await page.query_selector_all(selector)
                        for el in elements:
                            if await el.is_visible():
                                logger.debug("Clicking affirmative action as fallback: %s", selector)
                                await el.click(timeout=2000)
                                await page.wait_for_timeout(1000)
                                action_taken = True
                    except Exception:
                        continue

            if action_taken:
                # Give it a moment to process the action and potentially close the modal
                await page.wait_for_timeout(1000)

            # 3. Dismissal (fallback if affirmative actions didn't close it or weren't found)
            for selector in DISMISS_SELECTORS:
                try:
                    elements = await page.query_selector_all(selector)
                    for el in elements:
                        if await el.is_visible():
                            logger.debug("Clicking dismiss action in overlay: %s", selector)
                            await el.click(timeout=2000)
                            await page.wait_for_timeout(500)
                except Exception:
                    continue

            # Final fallback: escape key
            await page.keyboard.press('Escape')
            await page.wait_for_timeout(500)

        except Exception as e:
            logger.error("Error while trying to handle overlay: %s", e)
