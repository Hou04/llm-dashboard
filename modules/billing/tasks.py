import logging
from datetime import datetime, timedelta, timezone
from celery import shared_task
from core.database import AsyncSessionLocal
from modules.billing.services.billing_service import BillingService

logger = logging.getLogger(__name__)

@shared_task(name="modules.billing.tasks.generate_monthly_drafts")
async def generate_monthly_drafts(year_month: int = None):
    """
    Automated task to generate draft invoices for all active tenants.
    Usually runs on the 1st of each month for the previous month.
    """
    if year_month is None:
        # Calculate previous month (YYYYMM)
        now = datetime.now(timezone.utc)
        first_of_this_month = now.replace(day=1)
        last_day_prev_month = first_of_this_month - timedelta(days=1)
        year_month = int(last_day_prev_month.strftime("%Y%m"))

    logger.info(f"Starting automated draft generation for {year_month}")
    
    async with AsyncSessionLocal() as session:
        try:
            service = BillingService(session)
            result = await service.generate_all_for_month(year_month, finalize=False)
            await session.commit()
            
            logger.info(
                f"Automated billing completed: "
                f"{result['succeeded']} succeeded, {result['failed']} failed."
            )
            return result
        except Exception as e:
            await session.rollback()
            logger.error(f"Automated billing failed: {str(e)}")
            raise e
