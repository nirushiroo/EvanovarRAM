"""
features/account_creator.py
Generate Roblox signup details and open account creation browsers.
"""

from __future__ import annotations

from datetime import datetime
import json
from queue import Empty, Queue
import re
import secrets
import string
import threading
from typing import Callable

from classes.operation_result import OperationResult, ensure_result
from features.account_actions import get_browser_result

MAX_CREATOR_BROWSERS = 5
MAX_CREATOR_ACCOUNTS = 100
MAX_USERNAME_LENGTH = 20
MAX_PREFIX_LENGTH = 14
RANDOM_SUFFIX_LENGTH = 6
_SIGNUP_URL = "https://www.roblox.com/CreateAccount"
_MONTHS = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)
_ADJECTIVES = (
    "Bright", "Calm", "Clever", "Cool", "Cosmic", "Happy", "Jolly",
    "Kind", "Lucky", "Mellow", "Mighty", "Neon", "Quick", "Silent",
    "Solar", "Swift", "Tiny", "Urban", "Wild", "Wise",
)
_NOUNS = (
    "Badger", "Beacon", "Comet", "Falcon", "Forest", "Fox", "Galaxy",
    "Harbor", "Koala", "Maple", "Meteor", "Otter", "Panda", "Pixel",
    "Raven", "River", "Robin", "Tiger", "Voyager", "Wolf",
)


def _random_string(length: int) -> str:
    characters = string.ascii_letters + string.digits
    return "".join(secrets.choice(characters) for _ in range(max(1, length)))


def is_valid_prefix(value: str) -> bool:
    prefix = str(value or "")
    return bool(
        prefix
        and len(prefix) <= MAX_PREFIX_LENGTH
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_]*", prefix)
    )


def _generate_username(options: dict | None = None) -> str:
    settings = options or {}
    if settings.get("enable_custom_prefix"):
        prefix = str(settings.get("prefix", "") or "").strip()
        if not is_valid_prefix(prefix):
            raise ValueError("The custom prefix does not meet the requirements.")
        suffix = _random_string(RANDOM_SUFFIX_LENGTH)
        return f"{prefix}{suffix}"[:MAX_USERNAME_LENGTH]

    base = f"{secrets.choice(_ADJECTIVES)}{secrets.choice(_NOUNS)}"
    base_length = MAX_USERNAME_LENGTH - RANDOM_SUFFIX_LENGTH
    return f"{base[:base_length]}{_random_string(RANDOM_SUFFIX_LENGTH)}"[
        :MAX_USERNAME_LENGTH
    ]


def _generate_password(length: int = 18) -> str:
    characters = string.ascii_letters + string.digits + "!@#$%"
    required = [
        secrets.choice(string.ascii_uppercase),
        secrets.choice(string.ascii_lowercase),
        secrets.choice(string.digits),
        secrets.choice("!@#$%"),
    ]
    required.extend(secrets.choice(characters) for _ in range(length - 4))
    secrets.SystemRandom().shuffle(required)
    return "".join(required)


def _generate_signup_profile(options: dict | None = None) -> dict:
    settings = options or {}
    current_year = datetime.now().year
    year = current_year - (secrets.randbelow(27) + 19)
    candidates = []
    while len(candidates) < 10:
        username = _generate_username(settings)
        if username.lower() not in {candidate.lower() for candidate in candidates}:
            candidates.append(username)
    custom_password = str(settings.get("password", "") or "")
    return {
        "month": secrets.choice(_MONTHS),
        "day": f"{secrets.randbelow(28) + 1:02d}",
        "year": str(year),
        "usernames": candidates,
        "password": custom_password or _generate_password(),
    }


def _build_signup_script(profile: dict) -> str:
    """Build the in-page script that fills and submits the signup form.

    Handles both markups Roblox serves at /CreateAccount: the legacy form
    (real <select> elements, #MaleButton, #signup-button and the
    #signup-*InputValidation nodes) and the signup-v2 React card (Radix
    comboboxes, aria-pressed gender buttons, a submit button with no id, and
    a description node carrying the username error).
    """
    month = json.dumps(profile["month"])
    day = json.dumps(profile["day"])
    year = json.dumps(profile["year"])
    usernames = json.dumps(profile["usernames"])
    password = json.dumps(profile["password"])

    return f"""
    (function() {{
        var usernames = {usernames};
        var password = {password};
        var birthday = {{ month: {month}, day: {day}, year: {year} }};
        var usernameIndex = 0;
        var submitted = false;
        var partCooldown = [0, 0, 0];
        var partOpens = [0, 0, 0];
        var partTypes = [0, 0, 0];

        function reactChange(element) {{
            for (var key in element) {{
                if (key.indexOf('reactProps') !== -1) {{
                    var props = element[key];
                    if (props && props.onChange) {{
                        props.onChange({{ target: element }});
                    }}
                }}
            }}
        }}

        function setValue(element, value) {{
            var proto = null;
            if (element.tagName === 'SELECT' && window.HTMLSelectElement) {{
                proto = window.HTMLSelectElement.prototype;
            }} else if (window.HTMLInputElement) {{
                proto = window.HTMLInputElement.prototype;
            }}
            var descriptor = proto
                ? Object.getOwnPropertyDescriptor(proto, 'value')
                : null;
            if (descriptor && descriptor.set) {{
                descriptor.set.call(element, value);
            }} else {{
                element.value = value;
            }}
        }}

        function keyUp() {{
            try {{
                return new KeyboardEvent('keyup', {{
                    bubbles: true, cancelable: true, key: 'a',
                }});
            }} catch (error) {{
                return new Event('keyup', {{ bubbles: true }});
            }}
        }}

        function setInput(element, value) {{
            setValue(element, value);
            element.dispatchEvent(new Event('input', {{ bubbles: true }}));
            element.dispatchEvent(keyUp());
            element.dispatchEvent(new Event('change', {{ bubbles: true }}));
            reactChange(element);
        }}

        function optionMatches(node, value) {{
            var target = String(value).trim().toLowerCase();
            if (!target) {{
                return false;
            }}
            var optionValue = String(node.value || '').trim().toLowerCase();
            var optionLabel = nodeText(node).toLowerCase();
            if (optionValue && optionValue === target) {{
                return true;
            }}
            if (optionLabel === target || optionLabel.indexOf(target) === 0) {{
                return true;
            }}
            var strippedValue = optionValue.replace(/^0+/, '');
            var strippedTarget = target.replace(/^0+/, '');
            return !!strippedTarget && strippedValue === strippedTarget;
        }}

        function setSelect(element, value) {{
            var options = element.options || [];
            for (var index = 0; index < options.length; index++) {{
                if (!optionMatches(options[index], value)) {{
                    continue;
                }}
                setValue(element, options[index].value);
                options[index].selected = true;
                element.dispatchEvent(new Event('input', {{ bubbles: true }}));
                element.dispatchEvent(new Event('change', {{ bubbles: true }}));
                reactChange(element);
                return true;
            }}
            return false;
        }}

        function press(node) {{
            // Mirrors a real click so pointer-driven controls (the signup-v2
            // Radix dropdowns) react, not just click handlers.
            var types = [
                'pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click'
            ];
            for (var index = 0; index < types.length; index++) {{
                var options = {{
                    bubbles: true,
                    cancelable: true,
                    button: 0,
                    pointerType: 'mouse',
                    isPrimary: true,
                }};
                var event = null;
                try {{
                    event = window.PointerEvent
                        ? new window.PointerEvent(types[index], options)
                        : new MouseEvent(types[index], options);
                }} catch (error) {{
                    event = new MouseEvent(types[index], options);
                }}
                node.dispatchEvent(event);
            }}
        }}

        function query(selector, root) {{
            try {{
                return (root || document).querySelector(selector);
            }} catch (error) {{
                return null;
            }}
        }}

        function queryAll(selector, root) {{
            var found;
            try {{
                found = (root || document).querySelectorAll(selector);
            }} catch (error) {{
                return [];
            }}
            return Array.prototype.slice.call(found);
        }}

        function byId(id) {{
            return document.getElementById(id);
        }}

        function nodeText(node) {{
            return node ? (node.textContent || '').trim() : '';
        }}

        function usernameInput() {{
            return byId('signup-username')
                || query('input[name="signupUsername"]')
                || query('input[autocomplete="username"]')
                || query('input[type="text"]');
        }}

        function passwordInput() {{
            return byId('signup-password')
                || query('input[name="signupPassword"]')
                || query('input[autocomplete="new-password"]')
                || query('input[type="password"]');
        }}

        function submitButton() {{
            var legacy = byId('signup-button');
            if (legacy) {{
                return legacy;
            }}
            var candidates = queryAll('button[type="submit"]').concat(
                queryAll('button[aria-label="Create account"]')
            );
            candidates = candidates.concat(queryAll('form button'));
            for (var index = 0; index < candidates.length; index++) {{
                var label = nodeText(candidates[index]).toLowerCase();
                if (
                    label.indexOf('create account') === 0
                    || label.indexOf('sign up') === 0
                ) {{
                    return candidates[index];
                }}
            }}
            return query('button[type="submit"]');
        }}

        function isDisabled(button) {{
            if (!button) {{
                return true;
            }}
            if (button.disabled) {{
                return true;
            }}
            return !!(
                button.getAttribute
                && button.getAttribute('aria-disabled') === 'true'
            );
        }}

        function usernameWarningText(input) {{
            var node = byId('signup-usernameInputValidation');
            if (!node && input && input.getAttribute) {{
                var described = String(
                    input.getAttribute('aria-describedby') || ''
                ).split(' ');
                for (var index = 0; index < described.length; index++) {{
                    if (!described[index]) {{
                        continue;
                    }}
                    node = byId(described[index]);
                    if (node) {{
                        break;
                    }}
                }}
            }}
            if (!node && input && input.closest) {{
                var wrapper = input.closest('[data-testid="text-input-wrapper"]');
                if (wrapper) {{
                    node = query('[class*="content-system-alert"]', wrapper)
                        || query('[id$="-description"]', wrapper);
                }}
            }}
            return nodeText(node);
        }}

        function usernameRejected(warning) {{
            var text = String(warning || '').toLowerCase();
            if (!text) {{
                return false;
            }}
            var phrases = [
                'not appropriate', 'inappropriate', 'already in use',
                'already taken', 'is taken', 'not available', 'unavailable',
                'cannot be used', 'can not be used', 'not allowed',
                'invalid', 'must be', 'can only contain',
            ];
            for (var index = 0; index < phrases.length; index++) {{
                if (text.indexOf(phrases[index]) !== -1) {{
                    return true;
                }}
            }}
            return false;
        }}

        function birthdayPart(id, testId, name) {{
            var node = byId(id)
                || query('[data-testid="' + testId + '"]')
                || query('select[name="' + name + '"]');
            if (!node) {{
                return null;
            }}
            if (node.tagName === 'SELECT') {{
                return {{ select: node, button: null }};
            }}
            return {{
                select: query('select', node),
                button: query('button[role="combobox"]', node)
                    || query('button', node),
            }};
        }}

        function partIsSet(part, value) {{
            if (!part) {{
                return false;
            }}
            var current = part.button
                ? nodeText(part.button).toLowerCase()
                : String((part.select && part.select.value) || '').toLowerCase();
            if (!current) {{
                return false;
            }}
            var target = String(value).toLowerCase();
            return current === target || current.indexOf(target) === 0;
        }}

        function listboxFor(button) {{
            if (!button || !button.getAttribute) {{
                return null;
            }}
            var controls = button.getAttribute('aria-controls');
            if (controls && byId(controls)) {{
                return byId(controls);
            }}
            if (button.getAttribute('aria-expanded') === 'true') {{
                return query('[role="listbox"]');
            }}
            return null;
        }}

        function keyboard(key, keyCode, node) {{
            if (!node) {{
                return;
            }}
            var event = null;
            try {{
                event = new KeyboardEvent('keydown', {{
                    key: key,
                    bubbles: true,
                    cancelable: true,
                }});
            }} catch (error) {{
                event = new Event('keydown', {{ bubbles: true }});
            }}
            try {{
                Object.defineProperty(event, 'keyCode', {{
                    get: function() {{ return keyCode; }},
                }});
                Object.defineProperty(event, 'which', {{
                    get: function() {{ return keyCode; }},
                }});
            }} catch (error) {{}}
            node.dispatchEvent(event);
        }}

        function keyTarget(list) {{
            // Radix moves focus into the popup portal, so key events must be
            // dispatched inside that tree for its handlers to see them.
            var active = document.activeElement;
            if (active && list.contains(active)) {{
                return active;
            }}
            return list;
        }}

        function highlightedOption(list) {{
            var activeId = list.getAttribute
                ? list.getAttribute('aria-activedescendant')
                : null;
            var node = activeId ? byId(activeId) : null;
            if (!node) {{
                node = query('[data-highlighted]', list)
                    || query('[aria-selected="true"]', list);
            }}
            return node;
        }}

        function optionInList(list, value) {{
            var options = queryAll('[role="option"]', list);
            for (var index = 0; index < options.length; index++) {{
                if (optionMatches(options[index], value)) {{
                    return options[index];
                }}
            }}
            return null;
        }}

        function typeInto(list, text) {{
            var container = keyTarget(list);
            var upper = String(text).toUpperCase();
            for (var index = 0; index < text.length; index++) {{
                keyboard(
                    text.charAt(index),
                    upper.charCodeAt(index),
                    container
                );
            }}
        }}

        function openBirthdayPart(part, index) {{
            if (++partOpens[index] > 8) {{
                // Stop fighting a control we cannot drive; leave it to the
                // user rather than flapping the popup forever.
                partCooldown[index] = Number.MAX_VALUE;
                return;
            }}
            try {{
                part.button.focus();
            }} catch (error) {{}}
            // Opening is a toggle. ArrowDown opens a closed Radix select and
            // pointer events are only a fallback for triggers that ignore
            // keys, so this path can never close a popup that is showing.
            keyboard('ArrowDown', 40, part.button);
            if (listboxFor(part.button)) {{
                partCooldown[index] = Date.now() + 1100;
                return;
            }}
            press(part.button);
            partCooldown[index] = Date.now() + 1100;
        }}

        function interactOpenBirthdayPart(part, value, index) {{
            var list = listboxFor(part.button);
            if (!list) {{
                return;
            }}
            var highlighted = highlightedOption(list);
            if (highlighted && optionMatches(highlighted, value)) {{
                keyboard('Enter', 13, keyTarget(list));
                partCooldown[index] = Date.now() + 700;
                return;
            }}
            if (++partTypes[index] >= 4) {{
                var match = optionInList(list, value);
                if (match) {{
                    // Typeahead never took; try a pointer selection on the
                    // option itself as a fallback.
                    press(match);
                    partCooldown[index] = Date.now() + 900;
                }}
                return;
            }}
            // The combobox supports typeahead: typing the option's text moves
            // the highlight, and the next pass commits it with Enter.
            typeInto(list, String(value));
            partCooldown[index] = Date.now() + 500;
        }}

        function setBirthdayPart(part, value, index) {{
            if (!part) {{
                return false;
            }}
            if (!part.button) {{
                return part.select ? setSelect(part.select, value) : false;
            }}
            var list = listboxFor(part.button);
            if (list) {{
                // Popup is showing: type into it, but never press the trigger
                // again (that closes the popup before a selection lands).
                interactOpenBirthdayPart(part, value, index);
                return partIsSet(part, value);
            }}
            if (Date.now() < partCooldown[index]) {{
                return false;
            }}
            openBirthdayPart(part, index);
            return false;
        }}

        function applyBirthday() {{
            var parts = [
                birthdayPart('MonthDropdown', 'birthday-month', 'birthdayMonth'),
                birthdayPart('DayDropdown', 'birthday-day', 'birthdayDay'),
                birthdayPart('YearDropdown', 'birthday-year', 'birthdayYear'),
            ];
            var values = [birthday.month, birthday.day, birthday.year];
            var complete = true;
            var acted = false;
            for (var index = 0; index < parts.length; index++) {{
                if (!parts[index]) {{
                    complete = false;
                    continue;
                }}
                if (partIsSet(parts[index], values[index])) {{
                    continue;
                }}
                complete = false;
                if (acted) {{
                    // One dropdown per pass so popups never fight each other.
                    continue;
                }}
                acted = true;
                setBirthdayPart(parts[index], values[index], index);
            }}
            return complete;
        }}

        function applyUsername() {{
            var input = usernameInput();
            if (!input || usernameIndex >= usernames.length) {{
                return false;
            }}
            var wanted = usernames[usernameIndex];
            if (String(input.value || '') === wanted) {{
                return true;
            }}
            setInput(input, wanted);
            return String(input.value || '') === wanted;
        }}

        function applyPassword() {{
            var input = passwordInput();
            if (!input) {{
                return false;
            }}
            if (String(input.value || '') !== password) {{
                setInput(input, password);
            }}
            try {{
                sessionStorage.setItem('_ram_pw', password);
            }} catch (error) {{}}
            return String(input.value || '') === password;
        }}

        function applyGender() {{
            var legacy = byId('MaleButton');
            if (legacy) {{
                var icon = legacy.firstElementChild;
                if (
                    !icon
                    || String(icon.className || '').indexOf('gender-selected')
                        === -1
                ) {{
                    press(legacy);
                }}
                return true;
            }}
            var buttons = queryAll('button[aria-pressed]');
            for (var index = 0; index < buttons.length; index++) {{
                var label = nodeText(buttons[index]).toLowerCase();
                var maleIcon = query('[class*="head-male"]', buttons[index]);
                if (label !== 'male' && !maleIcon) {{
                    continue;
                }}
                if (buttons[index].getAttribute('aria-pressed') === 'true') {{
                    return true;
                }}
                press(buttons[index]);
                return true;
            }}
            return false;
        }}

        function tick(attemptsLeft) {{
            if (submitted) {{
                checkAfterSubmit(40);
                return;
            }}

            // React re-renders can drop values, so every pass re-checks and
            // re-applies the fields instead of filling them once.
            var birthdayDone = applyBirthday();
            var passwordDone = applyPassword();
            applyGender();
            var usernameDone = applyUsername();

            var warning = usernameWarningText(usernameInput());
            if (warning.toLowerCase().indexOf('birthday') !== -1) {{
                // signup-v2 blocks the other fields until the birthday is set.
                warning = '';
                birthdayDone = applyBirthday();
            }}

            if (usernameRejected(warning)) {{
                if (usernameIndex + 1 < usernames.length) {{
                    usernameIndex += 1;
                    applyUsername();
                }}
            }} else {{
                var button = submitButton();
                if (
                    birthdayDone && usernameDone && passwordDone
                    && !isDisabled(button)
                ) {{
                    submitted = true;
                    press(button);
                    checkAfterSubmit(40);
                    return;
                }}
            }}

            if (attemptsLeft > 0) {{
                setTimeout(function() {{ tick(attemptsLeft - 1); }}, 250);
            }}
        }}

        function checkAfterSubmit(attemptsLeft) {{
            var warning = usernameWarningText(usernameInput());
            if (usernameRejected(warning)) {{
                submitted = false;
                if (usernameIndex + 1 < usernames.length) {{
                    usernameIndex += 1;
                }}
                if (attemptsLeft > 0) {{
                    setTimeout(
                        function() {{ tick(attemptsLeft - 1); }},
                        900
                    );
                }}
                return;
            }}
            if (attemptsLeft > 0) {{
                setTimeout(
                    function() {{ checkAfterSubmit(attemptsLeft - 1); }},
                    1500
                );
            }}
        }}

        tick(600);
    }})();
    """


def create_accounts(
    manager,
    amount: int,
    options: dict | None = None,
    on_done: Callable[[bool, str], None] = lambda *_: None,
) -> None:
    try:
        requested_amount = int(amount)
    except (TypeError, ValueError):
        on_done(False, "Account amount must be a number.")
        return

    if requested_amount < 1 or requested_amount > MAX_CREATOR_ACCOUNTS:
        on_done(
            False,
            f"Account amount must be between 1 and {MAX_CREATOR_ACCOUNTS}.",
        )
        return

    settings = dict(options or {})
    if settings.get("enable_custom_prefix"):
        prefix = str(settings.get("prefix", "") or "").strip()
        if not is_valid_prefix(prefix):
            on_done(
                False,
                "The custom prefix must only contain letters, numbers, and _. "
                "It cannot start with _.",
            )
            return
        settings["prefix"] = prefix

    custom_password = str(settings.get("password", "") or "")
    if custom_password and len(custom_password) < 8:
        on_done(False, "A custom password must contain at least 8 characters.")
        return
    settings["password"] = custom_password

    browser_result = get_browser_result()
    if not browser_result:
        on_done(
            False,
            f"{browser_result.message}\n\nError code: {browser_result.code}",
        )
        return
    browser = browser_result.data.get("browser")

    def _worker():
        profiles = [
            _generate_signup_profile(settings)
            for _ in range(requested_amount)
        ]
        existing_before = set(manager.accounts.keys())

        try:
            worker_count = min(MAX_CREATOR_BROWSERS, requested_amount)
            pending_profiles = Queue()
            failure_results: list[OperationResult] = []
            failure_lock = threading.Lock()
            for index, profile in enumerate(profiles):
                pending_profiles.put((index, profile))

            def _browser_slot(slot_index: int):
                while True:
                    try:
                        index, profile = pending_profiles.get_nowait()
                    except Empty:
                        return

                    try:
                        print(
                            f"[Account Creator] Slot {slot_index + 1} starting "
                            f"account {index + 1}/{requested_amount}."
                        )
                        result = ensure_result(manager.add_account(
                            amount=1,
                            website=_SIGNUP_URL,
                            javascript_list=[_build_signup_script(profile)],
                            password_list=[profile["password"]],
                            browser=browser,
                            window_slot=slot_index,
                            window_slot_count=worker_count,
                        ))
                        if not result:
                            with failure_lock:
                                failure_results.append(result)
                    except Exception as e:
                        print(f"[Account Creator] Browser slot failed: {e}")
                    finally:
                        pending_profiles.task_done()

            slot_threads = [
                threading.Thread(
                    target=_browser_slot,
                    args=(slot_index,),
                    daemon=True,
                    name=f"account-creator-slot-{slot_index + 1}",
                )
                for slot_index in range(worker_count)
            ]
            for slot_thread in slot_threads:
                slot_thread.start()
            for slot_thread in slot_threads:
                slot_thread.join()

            new_names = sorted(set(manager.accounts.keys()) - existing_before)
            if new_names:
                name_preview = ", ".join(new_names[:10])
                if len(new_names) > 10:
                    name_preview += f", and {len(new_names) - 10} more"
                summary = (
                    f"Created {len(new_names)}/{requested_amount} account(s). "
                    + name_preview
                )
                on_done(True, summary)
            else:
                if failure_results:
                    first_failure = failure_results[0]
                    on_done(
                        False,
                        f"{first_failure.message}\n\n"
                        f"Error code: {first_failure.code}",
                    )
                else:
                    on_done(
                        False,
                        "No accounts were created. Complete any CAPTCHA shown "
                        "in the browser and try again if the signup timed out.",
                    )
        except Exception as e:
            print(f"[Account Creator] Failed: {e}")
            on_done(False, str(e))

    threading.Thread(
        target=_worker,
        daemon=True,
        name="account-creator",
    ).start()
