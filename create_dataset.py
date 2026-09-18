import pandas as pd
import random

# Create synthetic data
data = []
for _ in range(200):  # 200 students
    study_hours = random.randint(1, 15)
    attendance = random.randint(30, 100)
    assignment = random.randint(30, 100)
    cat = random.randint(30, 100)
    practical = random.randint(30, 100)
    
    # Logic: student passes if study_hours >= 8 and attendance >= 70 and scores >= 60
    if (study_hours >= 8 and attendance >= 70 and assignment >= 60 and cat >= 60 and practical >= 60):
        result = "PASS"
    elif (study_hours >= 10 and attendance >= 75):
        result = "PASS"
    elif (study_hours <= 4 or attendance <= 50):
        result = "FAIL"
    else:
        result = random.choice(["PASS", "FAIL"])  # Random for borderline cases
    
    data.append([study_hours, attendance, assignment, cat, practical, result])

# Create DataFrame
df = pd.DataFrame(data, columns=[
    "study_hours", 
    "attendance_percentage", 
    "assignment_score", 
    "cat_score", 
    "practical_score", 
    "final_result"
])

# Save to CSV
df.to_csv("student_performance.csv", index=False)

print("✅ Dataset created successfully!")
print(f"Total records: {len(df)}")
print(f"PASS count: {len(df[df['final_result'] == 'PASS'])}")
print(f"FAIL count: {len(df[df['final_result'] == 'FAIL'])}")