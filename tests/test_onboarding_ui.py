"""
Tests for onboarding UI formatting functions.
"""
import pytest
from onboarding import format_tutorial_message, create_tutorial_keyboard


# Tests for format_tutorial_message


def test_format_tutorial_message_basic():
    """Test format_tutorial_message with basic content."""
    content = {
        'title': '🎮 Welcome',
        'message': 'This is a test message'
    }
    
    result = format_tutorial_message(0, content)
    
    assert '<b>🎮 Welcome</b>' in result
    assert 'This is a test message' in result
    assert '\n\n' in result


def test_format_tutorial_message_html_escaping():
    """Test format_tutorial_message escapes HTML characters."""
    content = {
        'title': 'Test <script>alert("xss")</script>',
        'message': 'Message with <b>tags</b> & special chars'
    }
    
    result = format_tutorial_message(0, content)
    
    # HTML should be escaped
    assert '&lt;script&gt;' in result
    assert '&lt;b&gt;' in result
    assert '&amp;' in result
    # Should not contain unescaped tags
    assert '<script>' not in result
    assert '<b>tags</b>' not in result


def test_format_tutorial_message_empty_content():
    """Test format_tutorial_message with empty content."""
    content = {}
    
    result = format_tutorial_message(0, content)
    
    # Should handle missing keys gracefully
    assert '<b></b>' in result
    assert '\n\n' in result


def test_format_tutorial_message_multiline():
    """Test format_tutorial_message with multiline message."""
    content = {
        'title': 'Step 1',
        'message': 'Line 1\nLine 2\nLine 3'
    }
    
    result = format_tutorial_message(1, content)
    
    assert '<b>Step 1</b>' in result
    assert 'Line 1\nLine 2\nLine 3' in result


def test_format_tutorial_message_special_chars():
    """Test format_tutorial_message with special characters."""
    content = {
        'title': 'Test "quotes" & symbols',
        'message': 'Price: $10 < $20 > $5'
    }
    
    result = format_tutorial_message(0, content)
    
    # Quotes and ampersands should be escaped
    assert '&quot;' in result or '"' in result  # Both are valid
    assert '&amp;' in result
    assert '&lt;' in result
    assert '&gt;' in result


# Tests for create_tutorial_keyboard


def test_create_tutorial_keyboard_first_step():
    """Test create_tutorial_keyboard on first step (step 0)."""
    keyboard = create_tutorial_keyboard(step=0, total_steps=5, can_skip=True)
    
    # Should have inline_keyboard attribute
    assert hasattr(keyboard, 'inline_keyboard')
    buttons = keyboard.inline_keyboard
    
    # First row should only have "Далее" (no "Назад" on first step)
    assert len(buttons[0]) == 1
    assert buttons[0][0].text == "Далее ▶️"
    assert buttons[0][0].callback_data == "tutorial:next"
    
    # Second row should have progress indicator
    assert "Шаг 0 из 5" in buttons[1][0].text
    
    # Third row should have skip button
    assert "Пропустить" in buttons[2][0].text
    assert buttons[2][0].callback_data == "tutorial:skip"


def test_create_tutorial_keyboard_middle_step():
    """Test create_tutorial_keyboard on middle step."""
    keyboard = create_tutorial_keyboard(step=2, total_steps=5, can_skip=True)
    
    buttons = keyboard.inline_keyboard
    
    # First row should have both "Назад" and "Далее"
    assert len(buttons[0]) == 2
    assert buttons[0][0].text == "◀️ Назад"
    assert buttons[0][0].callback_data == "tutorial:prev"
    assert buttons[0][1].text == "Далее ▶️"
    assert buttons[0][1].callback_data == "tutorial:next"
    
    # Second row should have progress indicator
    assert "Шаг 2 из 5" in buttons[1][0].text
    
    # Third row should have skip button
    assert "Пропустить" in buttons[2][0].text


def test_create_tutorial_keyboard_last_step():
    """Test create_tutorial_keyboard on last step."""
    keyboard = create_tutorial_keyboard(step=5, total_steps=5, can_skip=True)
    
    buttons = keyboard.inline_keyboard
    
    # First row should have "Назад" and "Завершить" (not "Далее")
    assert len(buttons[0]) == 2
    assert buttons[0][0].text == "◀️ Назад"
    assert buttons[0][0].callback_data == "tutorial:prev"
    assert buttons[0][1].text == "✅ Завершить"
    assert buttons[0][1].callback_data == "tutorial:complete"
    
    # Second row should have progress indicator
    assert "Шаг 5 из 5" in buttons[1][0].text
    
    # Should NOT have skip button on last step
    assert len(buttons) == 2  # Only nav row and progress row


def test_create_tutorial_keyboard_no_skip():
    """Test create_tutorial_keyboard with can_skip=False."""
    keyboard = create_tutorial_keyboard(step=2, total_steps=5, can_skip=False)
    
    buttons = keyboard.inline_keyboard
    
    # Should have nav row and progress row, but no skip row
    assert len(buttons) == 2
    
    # Verify no skip button exists
    for row in buttons:
        for button in row:
            assert "Пропустить" not in button.text


def test_create_tutorial_keyboard_single_step():
    """Test create_tutorial_keyboard with single step tutorial."""
    keyboard = create_tutorial_keyboard(step=1, total_steps=1, can_skip=True)
    
    buttons = keyboard.inline_keyboard
    
    # First row should have "Назад" and "Завершить"
    assert len(buttons[0]) == 2
    assert buttons[0][0].text == "◀️ Назад"
    assert buttons[0][1].text == "✅ Завершить"
    
    # Progress should show "Шаг 1 из 1"
    assert "Шаг 1 из 1" in buttons[1][0].text


def test_create_tutorial_keyboard_step_zero_total_zero():
    """Test create_tutorial_keyboard at step 0 with total 0."""
    keyboard = create_tutorial_keyboard(step=0, total_steps=0, can_skip=True)
    
    buttons = keyboard.inline_keyboard
    
    # Should have "Завершить" button (step == total_steps)
    assert buttons[0][0].text == "✅ Завершить"
    assert buttons[0][0].callback_data == "tutorial:complete"
    
    # Progress indicator
    assert "Шаг 0 из 0" in buttons[1][0].text


def test_create_tutorial_keyboard_progress_format():
    """Test create_tutorial_keyboard progress indicator format."""
    keyboard = create_tutorial_keyboard(step=3, total_steps=7, can_skip=True)
    
    buttons = keyboard.inline_keyboard
    
    # Find progress button (second row)
    progress_button = buttons[1][0]
    
    # Should have exact format "Шаг X из Y"
    assert progress_button.text == "Шаг 3 из 7"
    assert progress_button.callback_data == "tutorial:progress"


def test_create_tutorial_keyboard_button_structure():
    """Test create_tutorial_keyboard returns proper InlineKeyboardMarkup structure."""
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    
    keyboard = create_tutorial_keyboard(step=1, total_steps=3, can_skip=True)
    
    # Should be InlineKeyboardMarkup instance
    assert isinstance(keyboard, InlineKeyboardMarkup)
    
    # Should have inline_keyboard attribute with list of lists
    assert hasattr(keyboard, 'inline_keyboard')
    assert isinstance(keyboard.inline_keyboard, list)
    
    # Each row should be a list of InlineKeyboardButton
    for row in keyboard.inline_keyboard:
        assert isinstance(row, list)
        for button in row:
            assert isinstance(button, InlineKeyboardButton)
            assert hasattr(button, 'text')
            assert hasattr(button, 'callback_data')
