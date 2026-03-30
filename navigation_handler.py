from typing import List, Set, Optional, Dict, Any
from playwright.async_api import Page, Locator
from urllib.parse import urlparse
from config_loader import Config
import hashlib


class NavigationHandler:
    """Handle smart navigation through website."""

    def __init__(self, config: Config):
        self.config = config
        self.visited_urls: Set[str] = set()
        self.visited_dom_hashes: Set[str] = set()
        self.current_depth = 0
        self.clicks_on_current_page = 0

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

    DESTRUCTIVE_PATTERNS = [
        'logout', 'delete', 'remove', 'destroy', 'clear',
        'close', 'cancel', 'dismiss', 'no thanks', 'x'
    ]

    async def _is_destructive_action(self, element, text: str = "") -> bool:
        """Check if element action is destructive or dismissive (logout, delete, close, etc.)."""
        if not text:
            try:
                text = await element.text_content() or ""
            except:
                text = ""

        text_lower = text.lower()

        # Gather all relevant attributes
        href = ""
        classes = ""
        element_id = ""
        aria_label = ""
        try:
            href = (await element.get_attribute('href') or "").lower()
        except:
            pass
        try:
            classes = (await element.get_attribute('class') or "").lower()
            element_id = (await element.get_attribute('id') or "").lower()
        except:
            pass
        try:
            aria_label = (await element.get_attribute('aria-label') or "").lower()
        except:
            pass

        searchable = [text_lower, href, classes, element_id, aria_label]

        # Check user-configured exclude patterns
        for pattern in self.config.exclude_patterns:
            pattern_lower = pattern.lower()
            if any(pattern_lower in s for s in searchable):
                return True

        # Check built-in destructive/dismissive patterns
        for pattern in self.DESTRUCTIVE_PATTERNS:
            if any(pattern in s for s in searchable):
                return True

        return False

    async def _get_dom_hash(self, page: Page) -> str:
        """Generate hash of current DOM state."""
        try:
            dom_content = await page.content()
            # Remove dynamic parts that change but don't affect structure
            import re
            # Remove timestamps, random IDs, etc.
            cleaned = re.sub(r'id="[^"]*\d{10,}[^"]*"', 'id="dynamic"', dom_content)
            cleaned = re.sub(r'data-[^=]*="[^"]*\d{10,}[^"]*"', '', cleaned)
            return hashlib.md5(cleaned.encode()).hexdigest()
        except:
            return ""

    async def get_clickable_elements(self, page: Page) -> List[Locator]:
        """Get all clickable elements on current page."""
        clickable_selectors = [
            'a[href]',
            'button:not([disabled])',
            'input[type="submit"]:not([disabled])',
            '[onclick]',
            '[role="button"]',
            '[role="link"]',
            '[role="menuitem"]',
            'input[type="button"]:not([disabled])'
        ]

        all_elements = []
        seen_elements = set()

        for selector in clickable_selectors:
            try:
                page_elements = await page.query_selector_all(selector)
                for idx, elem in enumerate(page_elements):
                    try:
                        # Create locator for this specific element
                        locator = page.locator(selector).nth(idx)

                        # Get element identifier for deduplication
                        try:
                            elem_html = await elem.evaluate('el => el.outerHTML')
                            if elem_html in seen_elements:
                                continue
                            seen_elements.add(elem_html)
                        except:
                            pass

                        # Check if visible and not destructive
                        try:
                            if await locator.is_visible():
                                # Additional check: ensure button is actually enabled
                                # Some frameworks use aria-disabled instead of disabled attribute
                                is_enabled = await elem.evaluate('''el => {
                                    if (el.disabled) return false;
                                    if (el.getAttribute('aria-disabled') === 'true') return false;
                                    // Check computed style for pointer-events
                                    const style = window.getComputedStyle(el);
                                    if (style.pointerEvents === 'none') return false;
                                    return true;
                                }''')

                                if is_enabled and not await self._is_destructive_action(locator):
                                    all_elements.append(locator)
                                    if len(all_elements) >= self.config.max_clicks_per_page:
                                        return all_elements
                        except:
                            continue
                    except:
                        continue
            except:
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

            # Check DOM hash to avoid infinite loops
            dom_hash = await self._get_dom_hash(page)
            if dom_hash and dom_hash in self.visited_dom_hashes:
                return False

            self.visited_dom_hashes.add(dom_hash)
            return True

        except Exception as e:
            print(f"Navigation error to {url}: {e}")
            return False

    async def click_element(self, page: Page, element: Locator) -> bool:
        """Click element and handle navigation."""
        if self.clicks_on_current_page >= self.config.max_clicks_per_page:
            return False

        try:
            # Scroll element into view
            await element.scroll_into_view_if_needed()
            await page.wait_for_timeout(500)

            # Get URL before click
            url_before = page.url

            try:
                # Click element
                await element.click(timeout=5000)
            except Exception as click_err:
                error_msg = str(click_err)
                if 'intercepts pointer events' in error_msg:
                    print("Element click intercepted (likely by a modal). Attempting to interact with overlay...")
                    await self._handle_overlay(page)
                    
                    try:
                        # Try clicking again
                        await element.click(timeout=3000)
                    except Exception as retry_err:
                        print(f"Overlay still present, forcing click... ({retry_err})")
                        await element.click(timeout=3000, force=True)
                else:
                    raise click_err
            
            self.clicks_on_current_page += 1

            # Wait for navigation or network activity
            try:
                await page.wait_for_load_state('networkidle', timeout=self.config.wait_timeout)
            except:
                await page.wait_for_timeout(2000)

            # Check if URL changed
            url_after = page.url

            # If SPA, wait a bit more for async updates
            if url_before == url_after:
                await page.wait_for_timeout(1000)

            return True

        except Exception as e:
            print(f"Click error: {e}")
            return False

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
        except:
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
                import re
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

        except:
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

    async def fill_page_forms(self, page: Page):
        """Fill forms on the page to enable submit buttons."""
        if not self.config.form_filling or not self.config.form_filling.enabled:
            return

        max_passes = 3
        for pass_idx in range(max_passes):
            fields_filled = await self._fill_page_forms_pass(page, pass_idx)
            if fields_filled == 0:
                break
            # Wait a little before the next pass to allow UI to update
            await page.wait_for_timeout(500)

    async def _fill_page_forms_pass(self, page: Page, pass_idx: int = 0) -> int:
        fields_filled = 0
        try:
            # Find all visible inputs, textareas, and selects that are not disabled or readonly
            # Include readonly inputs as they might be custom click-triggered dropdowns
            inputs = await page.query_selector_all('input:not([type="hidden"]):not([disabled]), textarea:not([disabled]):not([readonly]), select:not([disabled])')

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
                            print(f"Selected select option: {selected_val}{el_label}")
                        elif len(options_data) > 1:
                            await input_el.select_option(index=1)
                            print(f"Selected select option by index 1{el_label}")
                        else:
                            await input_el.select_option(index=0)
                            print(f"Selected select option by index 0{el_label}")
                            
                        await input_el.dispatch_event('change')
                        await page.wait_for_timeout(300)
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
                        print(f"Adjusted value to meet minimum length {min_length}")

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
                                        await opt.scroll_into_view_if_needed()
                                        await opt.click(timeout=2000)
                                        print(f"Selected click-triggered dropdown option: {opt_selector}")
                                        dropdown_handled = True
                                        await page.wait_for_timeout(300)
                                        break
                                if dropdown_handled:
                                    break
                            except Exception:
                                continue
                    except Exception as e:
                        print(f"Error checking click dropdown: {e}")

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
                                        await opt.scroll_into_view_if_needed()
                                        await opt.click(timeout=2000)
                                        print(f"Selected typing-triggered dropdown option: {opt_selector}")
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
                                            await opt.scroll_into_view_if_needed()
                                            await opt.click(timeout=2000)
                                            print(f"Selected cleared-typing dropdown option: {opt_selector}")
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
                        print(f"Filled form field via dropdown selection{el_label}")
                    else:
                        print(f"Filled form field with: {fill_value}{el_label}")
                        
                    fields_filled += 1
                    await page.wait_for_timeout(self.config.form_filling.fill_delay)

                except Exception as e:
                    # Ignore errors for individual fields
                    continue

            # After filling all fields, wait a bit more for buttons to enable
            await page.wait_for_timeout(500)

        except Exception as e:
            print(f"Error filling forms: {e}")

        return fields_filled

    async def _handle_overlay(self, page: Page):
        """Attempt to interact with and then dismiss any blocking modals."""
        print("Handling overlay: filling forms and attempting affirmative actions first...")
        try:
            # 1. Fill any forms that might be in the new modal
            await self.fill_page_forms(page)
            
            # 2. Identify active modal container
            modal_container = None
            container_selectors = [
                'dialog[open]',
                '[role="dialog"]',
                '[role="alertdialog"]',
                '.modal-content',
                '.modal-dialog',
                '.modal',
                '[class*="modal"]',
                '.overlay',
                '[class*="overlay"]',
                '.cdk-overlay-container',
                '.cdk-overlay-pane',
                '[class*="cdk-overlay"]',
                '.mat-mdc-menu-panel',
                '[class*="mat-menu"]'
            ]
            
            for selector in container_selectors:
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
                print("Modal container identified. Searching for interactive elements...")
                try:
                    # Find buttons and links inside the modal
                    interactive_elements = await modal_container.query_selector_all('button, a[href], [role="button"], [role="menuitem"], input[type="submit"], input[type="button"]')
                    
                    for el in interactive_elements:
                        if not await el.is_visible():
                            continue

                        if await self._is_destructive_action(el):
                            continue

                        combined_text = ''
                        try:
                            combined_text = (await el.text_content() or '').strip()
                        except:
                            pass

                        print(f"Clicking actionable element in modal: '{combined_text[:30]}'")
                        try:
                            await el.click(timeout=2000)
                            await page.wait_for_timeout(1000)
                            action_taken = True
                        except Exception as click_err:
                            print(f"Could not click modal element: {click_err}")
                except Exception as e:
                    print(f"Error exploring modal elements: {e}")
            else:
                print("Could not explicitly identify modal container. Falling back to targeted selectors.")
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
                                print(f"Clicking affirmative action as fallback: {selector}")
                                await el.click(timeout=2000)
                                await page.wait_for_timeout(1000)
                                action_taken = True
                    except Exception:
                        continue

            if action_taken:
                # Give it a moment to process the action and potentially close the modal
                await page.wait_for_timeout(1000)
            
            # 3. Dismissal (fallback if affirmative actions didn't close it or weren't found)
            dismiss_selectors = [
                'button[aria-label="Close"]',
                'button[aria-label="close"]',
                '.close-button',
                '.modal-close',
                'button:has-text("Close")',
                'button:has-text("Cancel")',
                'button:has-text("No thanks")',
                'button:has-text("Dismiss")',
            ]
            
            for selector in dismiss_selectors:
                try:
                    elements = await page.query_selector_all(selector)
                    for el in elements:
                        if await el.is_visible():
                            print(f"Clicking dismiss action in overlay: {selector}")
                            await el.click(timeout=2000)
                            await page.wait_for_timeout(500)
                except Exception:
                    continue
                    
            # Final fallback: escape key
            await page.keyboard.press('Escape')
            await page.wait_for_timeout(500)
            
        except Exception as e:
            print(f"Error while trying to handle overlay: {e}")

    def reset_page_counters(self):
        """Reset counters for new page."""
        self.clicks_on_current_page = 0
    
    def can_continue_navigation(self) -> bool:
        """Check if navigation can continue."""
        return self.current_depth < self.config.max_depth

