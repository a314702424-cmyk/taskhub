TaskHub WebApp - גרסה מוכנה לשרת (מעודכן)

מה עודכן בגרסה הזו:
1. שליחת סיכום משמרת רצה ברקע כדי למנוע Timeout ו-Internal Server Error.
2. תמיכה טובה יותר ב-SMTP:
   - פורט 587 עם STARTTLS
   - פורט 465 עם SSL
   - timeout קצר כדי לא להפיל את השרת
3. המנהל יכול לסנן ולצפות במשימות של כל עובד בנפרד מתוך הדשבורד.
4. run.py עודכן ל-debug=False לשרת.

הפעלת מייל עם Gmail:
1. היכנס לחשבון Gmail של העובד.
2. הפעל אימות דו-שלבי.
3. צור App Password.
4. בעמוד ניהול המשתמשים מלא עבור כל עובד:
   - Sender Email = כתובת המייל של העובד
   - SMTP Host = smtp.gmail.com
   - SMTP Port = 587
   - SMTP Username = כתובת הג'ימייל המלאה
   - SMTP Password = ה-App Password
   - Employer Target Email = כתובת המייל של המעסיק

הפעלה מקומית:
1. פתח CMD בתוך התיקייה
2. הרץ:
   pip install -r requirements.txt
   python run.py
3. פתח בדפדפן:
   http://127.0.0.1:5000

פריסה ל-Render:
1. העלה את כל תוכן התיקייה ל-GitHub.
2. ב-Render בחר New + -> Web Service.
3. חבר את ה-repo.
4. אם צריך להזין ידנית:
   Build Command: pip install -r requirements.txt
   Start Command: gunicorn run:app
5. כל שינוי חדש מעלים ל-GitHub ואז ב-Render: Manual Deploy -> Deploy latest commit

חשוב:
- אם אתה מגיע מגרסה ישנה, מחק את instance/app.db לפני ההעלאה מחדש כדי לאפס מסד ישן.
- משתמש ראשוני:
  username: admin
  password: admin123


גרסה V4:
- תמיכה טובה יותר ב-Render בתשלום עם DATABASE_URL למסד נתונים חיצוני
- יצוא מלא של הגדרות + עובדים + משימות + היסטוריית עדכונים
- ייבוא מלא של כל הנתונים מתוך קובץ JSON אחד
- שיפור תאימות ל-Postgres ב-Render

המלצה חשובה:
ב-Render חינמי קבצי SQLite מקומיים לא נשמרים קבוע. כדי שהנתונים לא יימחקו צריך או:
1. מסד נתונים Render Postgres
2. או דיסק קבוע בשירות בתשלום

בגרסה הזו מומלץ מאוד להשתמש ב-Render Postgres ולהגדיר למערכת Environment Variable בשם DATABASE_URL.


עדכון V5
- נוספו כפתורי יצוא וייבוא גלויים במסך ההגדרות.
- במסך ההגדרות אמור להופיע טקסט: "גרסה פעילה: V5".
- אם אינך רואה את V5, בצע Ctrl+F5 בדפדפן ולאחר מכן Manual Deploy ב-Render.
