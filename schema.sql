CREATE DATABASE IF NOT EXISTS student_prediction CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE student_prediction;

CREATE TABLE IF NOT EXISTS users (
 id INT AUTO_INCREMENT PRIMARY KEY,
 username VARCHAR(100) NOT NULL UNIQUE,
 email VARCHAR(150) NULL UNIQUE,
 password VARCHAR(255) NOT NULL,
 role VARCHAR(30) DEFAULT 'user',
 security_question VARCHAR(255) NULL,
 security_answer VARCHAR(255) NULL,
 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS students (
 id INT AUTO_INCREMENT PRIMARY KEY,
 student_name VARCHAR(150) NOT NULL,
 trade VARCHAR(100) NULL,
 level VARCHAR(20) NULL,
 semester VARCHAR(20) NULL,
 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS predictions (
 id INT AUTO_INCREMENT PRIMARY KEY,
 student_id INT NULL,
 username VARCHAR(100) NOT NULL,
 subject VARCHAR(150) NOT NULL,
 study_hours DECIMAL(5,2) NOT NULL,
 attendance DECIMAL(5,2) NOT NULL,
 assignment DECIMAL(5,2) NOT NULL,
 cat DECIMAL(5,2) NOT NULL,
 practical DECIMAL(5,2) NOT NULL,
 prediction VARCHAR(20) NOT NULL,
 probability DECIMAL(6,4) NOT NULL,
 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
 INDEX idx_predictions_username (username),
 INDEX idx_predictions_subject (subject),
 FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE SET NULL
);
