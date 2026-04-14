"""Element classification: destructive action detection, date picker detection."""
import logging
from config_loader import Config
from css_selectors import DATE_PICKER_PATTERNS, DATE_INPUT_TYPES

logger = logging.getLogger(__name__)


class ElementClassifier:
    """Classify page elements by type (destructive, date picker, calendar overlay)."""

    DESTRUCTIVE_PATTERNS = [
        'logout', 'delete', 'remove', 'destroy', 'clear',
        'close', 'cancel', 'dismiss', 'no thanks',
    ]

    # Patterns that should only match the visible text content exactly (stripped),
    # not as substrings in URLs, classes, or other attributes.
    DESTRUCTIVE_TEXT_EXACT = ['x', '\u00d7']  # "x" and "×" (close buttons)

    def __init__(self, config: Config):
        self.config = config

    async def is_destructive_action(self, element, text: str = "") -> bool:
        """Check if element action is destructive or dismissive (logout, delete, close, etc.)."""
        if not text:
            try:
                text = await element.text_content() or ""
            except Exception:
                text = ""

        text_lower = text.strip().lower()

        # Gather all relevant attributes
        href = ""
        classes = ""
        element_id = ""
        aria_label = ""
        try:
            href = (await element.get_attribute('href') or "").lower()
        except Exception:
            pass
        try:
            classes = (await element.get_attribute('class') or "").lower()
            element_id = (await element.get_attribute('id') or "").lower()
        except Exception:
            pass
        try:
            aria_label = (await element.get_attribute('aria-label') or "").lower()
        except Exception:
            pass

        searchable = [text_lower, href, classes, element_id, aria_label]

        # Check user-configured exclude patterns
        for pattern in self.config.exclude_patterns:
            pattern_lower = pattern.lower()
            if any(pattern_lower in s for s in searchable):
                return True

        # Check built-in destructive/dismissive patterns (substring match)
        for pattern in self.DESTRUCTIVE_PATTERNS:
            if any(pattern in s for s in searchable):
                return True

        # Check exact-text-only patterns (e.g. "x" close buttons)
        if text_lower in self.DESTRUCTIVE_TEXT_EXACT:
            return True

        return False

    async def is_date_picker_element(self, element) -> bool:
        """Check if element is a date picker trigger that should be skipped."""
        # Check input type
        try:
            input_type = (await element.get_attribute('type') or "").lower()
            if input_type in DATE_INPUT_TYPES:
                return True
        except Exception:
            pass

        # Check class, id, aria-label, name against date picker patterns
        attrs = []
        try:
            attrs.append((await element.get_attribute('class') or "").lower())
        except Exception:
            pass
        try:
            attrs.append((await element.get_attribute('id') or "").lower())
        except Exception:
            pass
        try:
            attrs.append((await element.get_attribute('aria-label') or "").lower())
        except Exception:
            pass
        try:
            attrs.append((await element.get_attribute('name') or "").lower())
        except Exception:
            pass

        for attr_val in attrs:
            for pattern in DATE_PICKER_PATTERNS:
                if pattern in attr_val:
                    return True

        # Check if element is inside a date picker component or adjacent to a date input
        try:
            is_date_related = await element.evaluate('''(el) => {
                const pickerAncestor = el.closest(
                    '[class*="datepicker"], [class*="date-picker"], [class*="calendar"], '
                  + '[class*="flatpickr"], [class*="mat-datepicker"], [class*="ant-picker"], '
                  + '[class*="react-datepicker"]'
                );
                if (pickerAncestor) return true;
                const parent = el.parentElement;
                if (parent) {
                    const dateInput = parent.querySelector(
                        'input[type="date"], input[type="datetime-local"], '
                      + 'input[type="time"], input[type="month"], input[type="week"]'
                    );
                    if (dateInput) return true;
                }
                return false;
            }''')
            if is_date_related:
                return True
        except Exception:
            pass

        return False

    async def is_calendar_overlay(self, container) -> bool:
        """Check if a container element looks like a calendar overlay."""
        try:
            return await container.evaluate('''(el) => {
                const cls = (el.className || '').toLowerCase();
                const calendarPatterns = ['calendar', 'datepicker', 'date-picker', 'flatpickr'];
                if (calendarPatterns.some(p => cls.includes(p))) return true;
                const grid = el.querySelector('[role="grid"]');
                if (grid) {
                    const cells = grid.querySelectorAll('td, [role="gridcell"]');
                    let dayCount = 0;
                    cells.forEach(c => {
                        const num = parseInt(c.textContent.trim());
                        if (num >= 1 && num <= 31) dayCount++;
                    });
                    if (dayCount >= 7) return true;
                }
                return false;
            }''')
        except Exception:
            return False
