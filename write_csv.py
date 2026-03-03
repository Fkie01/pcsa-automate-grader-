import csv
import os

def write_student_result(
    filename,
    student_name,
    m1_pass=0, m1_score=0,
    m2_pass=0, m2_score=0,
    m3_pass=0, m3_score=0
):
    """
    Write one student's milestone results to CSV.

    Missing milestones default to 0.
    Total score is automatically calculated.
    """

    # Ensure None becomes 0
    m1_pass = m1_pass or 0
    m1_score = m1_score or 0
    m2_pass = m2_pass or 0
    m2_score = m2_score or 0
    m3_pass = m3_pass or 0
    m3_score = m3_score or 0

    total = m1_score + m2_score + m3_score

    file_exists = os.path.isfile(filename)

    with open(filename, mode="a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)

        # Write header only once
        if not file_exists:
            writer.writerow([
                "student_name",
                "m1_pass", "m1_score",
                "m2_pass", "m2_score",
                "m3_pass", "m3_score",
                "total"
            ])

        writer.writerow([
            student_name,
            m1_pass, m1_score,
            m2_pass, m2_score,
            m3_pass, m3_score,
            total
        ])