"""Form filling logic for web crawling."""
import logging
import re
from playwright.async_api import Page
from config_loader import Config

logger = logging.getLogger(__name__)


class FormFiller:
    """Fill forms on pages to enable submit buttons and trigger API calls."""

    def __init__(self, config: Config):
        self.config = config

    async def _get_element_label(self, input_el) -> str:
        """Get a human-readable label for a form element."""
        try:
            text = (await input_el.evaluate('el => el.textContent') or "").strip()
            if not text:
                text = (await input_el.get_attribute('aria-label') or "").strip()
            if not text:
                text = (await input_el.get_attribute('placeholder') or "").strip()
            if not text:
                text = (await input_el.get_attribute('name') or "").strip()
            if not text:
                text = (await input_el.get_attribute('id') or "").strip()
            if text:
                return f" ('{text[:30]}')"
        except Exception:
            pass
        return ""

    async def _get_minimum_length(self, input_el) -> int:
        """Get minimum length requirement for input field."""
        try:
            # Check minlength attribute
            minlength = await input_el.get_attribute('minlength')
            if minlength and minlength.isdigit():
                return int(minlength)

            # Check pattern attribute for length hints
            pattern = await input_el.get_attribute('pattern')
            if pattern:
                # Simple regex patterns like .{8,} or .{8,20}
                match = re.search(r'\.{\s*(\d+)\s*,', pattern)
                if match:
                    return int(match.group(1))

            # Check required attribute and common validation patterns
            required = await input_el.get_attribute('required')
            if required is not None:
                input_type = await input_el.get_attribute('type') or 'text'
                # Common defaults for required fields
                if input_type == 'password':
                    return 8  # Common password minimum

        except Exception:
            pass

        return 0

    async def _generate_value_with_length(self, base_value: str, min_length: int) -> str:
        """Generate a value that meets minimum length requirement."""
        if len(base_value) >= min_length:
            return base_value

        # Pad the value to meet minimum length
        if '@' in base_value:  # Email
            # Add characters before @
            local_part, domain = base_value.split('@', 1)
            padding_needed = min_length - len(base_value)
            local_part += 'x' * padding_needed
            return f"{local_part}@{domain}"
        elif base_value.startswith('http'):  # URL
            padding_needed = min_length - len(base_value)
            return base_value + 'x' * padding_needed
        else:  # Regular text or password
            padding_needed = min_length - len(base_value)
            return base_value + 'x' * padding_needed

    async def fill_page_forms(self, page: Page, root=None):
        """Fill forms on the page (or within a specific container) to enable submit buttons."""
        if not self.config.form_filling or not self.config.form_filling.enabled:
            return

        max_passes = 3
        for pass_idx in range(max_passes):
            fields_filled = await self._fill_page_forms_pass(page, pass_idx, root=root)
            if fields_filled == 0:
                break
            # Wait a little before the next pass to allow UI to update
            await page.wait_for_timeout(500)

    async def _fill_page_forms_pass(self, page: Page, pass_idx: int = 0, root=None) -> int:
        fields_filled = 0
        query_root = root or page
        try:
            # Find all visible inputs, textareas, and selects that are not disabled or readonly
            # Include readonly inputs as they might be custom click-triggered dropdowns
            inputs = await query_root.query_selector_all('input:not([type="hidden"]):not([disabled]), textarea:not([disabled]):not([readonly]), select:not([disabled])')

            for input_el in inputs:
                try:
                    if not await input_el.is_visible():
                        continue

                    tag_name = await input_el.evaluate('el => el.tagName.toLowerCase()')

                    if tag_name == 'select':
                        current_val = await input_el.evaluate('el => el.value')
                        if pass_idx > 0 and current_val and current_val.strip() != '':
                            continue
                        options_data = await input_el.evaluate('''el => {
                            return Array.from(el.options).map((o, idx) => ({
                                index: idx,
                                value: o.value,
                                disabled: o.disabled
                            }));
                        }''')

                        if not options_data:
                            continue

                        valid_options = [o for o in options_data if not o.get('disabled') and o.get('value', '').strip() != '']

                        if current_val and current_val.strip() != '' and any(o.get('value') == current_val for o in valid_options):
                            continue

                        el_label = await self._get_element_label(input_el)
                        if valid_options:
                            selected_val = valid_options[0]['value']
                            await input_el.select_option(value=selected_val)
                            logger.debug("Selected select option: %s%s", selected_val, el_label)
                        elif len(options_data) > 1:
                            await input_el.select_option(index=1)
                            logger.debug("Selected select option by index 1%s", el_label)
                        else:
                            await input_el.select_option(index=0)
                            logger.debug("Selected select option by index 0%s", el_label)

                        await input_el.dispatch_event('change')
                        await page.wait_for_timeout(self.config.form_filling.fill_delay)
                        fields_filled += 1
                        continue

                    # Check if already has value (use DOM property on subsequent passes
                    # since get_attribute only reads the initial HTML attribute)
                    if pass_idx > 0:
                        current_value = await input_el.evaluate('el => el.value')
                    else:
                        current_value = await input_el.get_attribute('value')
                    if current_value:
                        continue

                    # Get minimum length requirement
                    min_length = await self._get_minimum_length(input_el)

                    # Determine value to fill
                    fill_value = "Test Value"

                    # check specific defaults from config first
                    if self.config.form_filling.defaults:
                        # naive check using selector matching - in real world might need more robust matching
                        for selector, value in self.config.form_filling.defaults.items():
                            is_match = await input_el.evaluate(f'(el) => el.matches("{selector}")')
                            if is_match:
                                fill_value = value
                                break

                    # If no specific default, guess based on type/name
                    if fill_value == "Test Value":
                        input_type = await input_el.get_attribute('type') or 'text'
                        input_name = await input_el.get_attribute('name') or ''
                        input_id = await input_el.get_attribute('id') or ''

                        lower_name = (input_name + input_id).lower()

                        if input_type == 'email' or 'email' in lower_name:
                            fill_value = "test@example.com"
                        elif input_type == 'password' or 'password' in lower_name:
                            fill_value = "Password123!"
                        elif input_type == 'tel' or 'phone' in lower_name:
                            fill_value = "555-012345"
                        elif input_type == 'number':
                            fill_value = "1"
                        elif input_type == 'url':
                            fill_value = "https://example.com"
                        elif input_type == 'date':
                            fill_value = "2024-01-01"

                    # Ensure value meets minimum length requirement
                    if min_length > 0:
                        fill_value = await self._generate_value_with_length(fill_value, min_length)
                        logger.debug("Adjusted value to meet minimum length %d", min_length)

                    # Clear the field first
                    try:
                        await input_el.clear(timeout=2000)
                    except Exception:
                        pass # Native readonly fields might throw here

                    # Check for click-triggered dropdowns
                    dropdown_handled = False
                    try:
                        await input_el.click(timeout=2000)
                        await page.wait_for_timeout(500)

                        option_selectors = [
                            '[role="option"]',
                            '.dropdown-item',
                            '.select2-results__option',
                            '.ant-select-item-option',
                            '.el-select-dropdown__item',
                            '.mat-option',
                            '.v-list-item'
                        ]

                        for opt_selector in option_selectors:
                            try:
                                options = await page.query_selector_all(opt_selector)
                                for opt in options:
                                    if await opt.is_visible():
                                        # Scroll into view and click
                                        try:
                                            await opt.scroll_into_view_if_needed(timeout=2000)
                                        except Exception:
                                            pass
                                        await opt.click(timeout=2000)
                                        logger.debug("Selected click-triggered dropdown option: %s", opt_selector)
                                        dropdown_handled = True
                                        await page.wait_for_timeout(300)
                                        break
                                if dropdown_handled:
                                    break
                            except Exception:
                                continue
                    except Exception as e:
                        logger.warning("Error checking click dropdown: %s", e)

                    if not dropdown_handled:
                        # Fill the value character by character to trigger validation
                        try:
                            await input_el.type(fill_value, delay=50)
                        except Exception:
                            # Might fail if readonly, but we tried our best
                            pass

                        await page.wait_for_timeout(300)

                        # Also check if typing triggered an autocomplete dropdown
                        try:
                            for opt_selector in option_selectors:
                                options = await page.query_selector_all(opt_selector)
                                for opt in options:
                                    if await opt.is_visible():
                                        try:
                                            await opt.scroll_into_view_if_needed(timeout=2000)
                                        except Exception:
                                            pass
                                        await opt.click(timeout=2000)
                                        logger.debug("Selected typing-triggered dropdown option: %s", opt_selector)
                                        dropdown_handled = True
                                        await page.wait_for_timeout(300)
                                        break
                                if dropdown_handled:
                                    break
                        except Exception:
                            pass

                        # If no dropdown option appeared after typing, it might be an autocomplete
                        # where only matching values show options. Clear input to see if it shows all options.
                        if not dropdown_handled:
                            try:
                                # First, clear the input
                                await input_el.fill("", timeout=1000)
                                await page.wait_for_timeout(300)

                                # Check again if any option appeared
                                for opt_selector in option_selectors:
                                    options = await page.query_selector_all(opt_selector)
                                    for opt in options:
                                        if await opt.is_visible():
                                            try:
                                                await opt.scroll_into_view_if_needed(timeout=2000)
                                            except Exception:
                                                pass
                                            await opt.click(timeout=2000)
                                            logger.debug("Selected cleared-typing dropdown option: %s", opt_selector)
                                            dropdown_handled = True
                                            await page.wait_for_timeout(300)
                                            break
                                    if dropdown_handled:
                                        break

                                # If STILL no dropdown, we re-type the original value to proceed normally
                                if not dropdown_handled:
                                    await input_el.type(fill_value, delay=50)
                                    await page.wait_for_timeout(300)
                            except Exception:
                                pass

                    # Dispatch events to ensure app logic detects change
                    try:
                        await input_el.dispatch_event('input')
                        await input_el.dispatch_event('change')
                        await input_el.dispatch_event('blur')
                    except Exception:
                        pass

                    # Wait for validation to run
                    await page.wait_for_timeout(300)

                    el_label = await self._get_element_label(input_el)
                    if dropdown_handled:
                        logger.debug("Filled form field via dropdown selection%s", el_label)
                    else:
                        logger.debug("Filled form field with: %s%s", fill_value, el_label)

                    fields_filled += 1
                    await page.wait_for_timeout(self.config.form_filling.fill_delay)

                except Exception as e:
                    # Ignore errors for individual fields
                    continue

            # After filling all fields, wait a bit more for buttons to enable
            await page.wait_for_timeout(500)

        except Exception as e:
            logger.error("Error filling forms: %s", e)

        return fields_filled
