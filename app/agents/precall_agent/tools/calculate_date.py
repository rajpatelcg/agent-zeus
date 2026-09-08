import datetime
from datetime import timedelta

def get_calculated_dates() -> str:
    """
    Returns a formatted string containing exact calendar dates for relative timeframes.
    This serves as a deterministic reference for the LLM to prevent calculation errors.
    """
    today = datetime.datetime.now()
    
    offsets = {
        "today": 0,
        "tomorrow": 1,
        "day after tomorrow": 2,
        "in 3 days": 3,
        "in 4 days": 4,
        "in 5 days": 5,
        "in 6 days": 6,
        "in a week": 7,
    }
    
    lines = ["## CALCULATED DATES REFERENCE:"]
    for label, days in offsets.items():
        target_date = today + timedelta(days=days)
        
        # Determine ordinal suffix
        day = target_date.day
        if 11 <= day <= 13:
            suffix = 'th'
        else:
            suffix = {1: 'st', 2: 'nd', 3: 'rd'}.get(day % 10, 'th')
            
        formatted_date = target_date.strftime(f"%A, %B %d{suffix}")
        lines.append(f"- {label}: {formatted_date}")
        
    return "\n".join(lines)
