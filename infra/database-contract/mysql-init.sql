CREATE DATABASE analytics;
CREATE USER 'analytics_owner'@'%' IDENTIFIED BY 'analytics-owner';
GRANT ALL PRIVILEGES ON analytics.* TO 'analytics_owner'@'%';
CREATE USER 'analytics'@'%' IDENTIFIED BY 'analytics';
GRANT SELECT ON analytics.* TO 'analytics'@'%';
CREATE TABLE analytics.orders (id integer PRIMARY KEY, total decimal(10,2) NOT NULL);
INSERT INTO analytics.orders (id, total) VALUES (1, 42.50);
