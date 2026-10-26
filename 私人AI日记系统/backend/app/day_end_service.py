"""Ordered day closing shared independently of route handlers."""
from . import daily_state_analysis_service, diary_operations_service


async def end_today() -> dict:
    state = await daily_state_analysis_service.analyze_today_state()
    diary = await diary_operations_service.generate_today_diary_payload()
    return {
        "date": diary["date"],
        "state": state,
        "diary": diary,
        "diary_url": f"/diaries/{diary['date']}",
    }
