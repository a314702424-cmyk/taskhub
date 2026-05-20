TaskHub V16

תיקון נקודתי ומלא להרשאת משתמש בכיר:
- טופס עריכת משתמש שולח edit_role נפרד כדי למנוע התנגשות עם role של טופס יצירת משתמש.
- routes.py קורא edit_role קודם, ורק אחר כך role.
- אם נבחרו הרשאות allowed_user_ids והתפקיד עדיין הגיע employee, המערכת מעלה אוטומטית ל-senior כדי לא לאבד את ההרשאות.
- /health מחזיר v16.
- לוגים חדשים: USER EDIT ROLE POSTED כולל RAW_ROLE ו-RAW_EDIT_ROLE.
