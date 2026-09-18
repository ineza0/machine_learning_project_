import pandas as pd
from sklearn.ensemble import RandomForestClassifier
import joblib

print('Training model...')
df = pd.read_csv('student_performance.csv')
features = ['study_hours','attendance_percentage','assignment_score','cat_score','practical_score']
X = df[features]
y = df['final_result'].map({'PASS': 1, 'FAIL': 0})
model = RandomForestClassifier(n_estimators=150, random_state=42)
model.fit(X, y)
joblib.dump(model, 'student_model.pkl')
print(f'Accuracy: {model.score(X,y)*100:.2f}%')
print('Subject is now free text and is stored as prediction metadata, not a model restriction.')
