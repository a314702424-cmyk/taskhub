TaskHub V13 - מוכן להעלאה ל-Render

מה תוקן:
1. יצירה אוטומטית של טבלאות V12/V13 בכל עליית שרת.
2. מילוי שיוכי משימות ישנות לטבלת TaskAssignment.
3. תיקון אזהרת SQLAlchemy בהרשאות משתמש בכיר.
4. נתיב תיקון ידני ללא Shell: /admin/repair-v12
5. נתיב בדיקה: /admin/v12-status
6. /health מחזיר v13 וגם מונה שיוכים והרשאות.

אחרי העלאה ו-Deploy:
1. פתח /health וודא version=v13.
2. היכנס כמנהל ופתח /admin/repair-v12 פעם אחת.
3. פתח /admin/v12-status ובדוק שה-assignments גדול מ-0 אם יש משימות קיימות.
