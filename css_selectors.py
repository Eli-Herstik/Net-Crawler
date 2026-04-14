"""CSS selector constants used across the crawler modules."""

# Selectors for interactive elements inside overlays/popups/modals
INTERACTIVE_SELECTORS = (
    'button, a[href], [role="button"], [role="menuitem"], '
    '[role="option"], input[type="submit"], input[type="button"]'
)

# Selectors for clickable elements on a page (superset with :not([disabled]) filters)
CLICKABLE_SELECTORS = [
    'a[href]',
    'button:not([disabled])',
    'input[type="submit"]:not([disabled])',
    '[onclick]',
    '[role="button"]',
    '[role="link"]',
    '[role="menuitem"]',
    'input[type="button"]:not([disabled])',
]

# Date picker patterns to skip (substring match against class, id, aria-label, name)
DATE_PICKER_PATTERNS = [
    'datepicker', 'date-picker', 'calendar', 'datetimepicker',
    'datetime-picker', 'daterangepicker', 'date-range-picker',
    'flatpickr', 'pikaday', 'react-datepicker', 'mat-datepicker',
    'ant-calendar', 'ant-picker',
]

# Input types that represent date/time pickers
DATE_INPUT_TYPES = {'date', 'datetime-local', 'time', 'month', 'week'}

# Selectors for calendar overlay containers
CALENDAR_OVERLAY_SELECTORS = [
    '[class*="datepicker"]',
    '[class*="date-picker"]',
    '[class*="calendar"]',
    '[class*="flatpickr-calendar"]',
    '[class*="react-datepicker"]',
    '.mat-datepicker-popup',
    '[role="dialog"]:has([role="grid"])',
]

# Selectors for modal/dialog containers
MODAL_CONTAINER_SELECTORS = [
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
    '[class*="mat-menu"]',
]

# Selectors for popup/menu containers (menus, dropdowns, listboxes)
POPUP_CONTAINER_SELECTORS = [
    '.cdk-overlay-pane',
    '[class*="cdk-overlay"]',
    '.mat-mdc-menu-panel',
    '[class*="mat-menu"]',
    '[role="menu"]',
    '[role="listbox"]',
    '.dropdown-menu',
    '[class*="dropdown"]',
]

# Selectors for dismissing overlays/modals
DISMISS_SELECTORS = [
    'button[aria-label="Close"]',
    'button[aria-label="close"]',
    '.close-button',
    '.modal-close',
    'button:has-text("Close")',
    'button:has-text("Cancel")',
    'button:has-text("No thanks")',
    'button:has-text("Dismiss")',
]
