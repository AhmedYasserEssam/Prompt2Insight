CREATE ROLE analytics_owner NOLOGIN;
CREATE ROLE analytics LOGIN PASSWORD 'analytics';
CREATE SCHEMA analytics AUTHORIZATION analytics_owner;
GRANT CONNECT ON DATABASE analytics TO analytics;
GRANT USAGE ON SCHEMA analytics TO analytics;
SET ROLE analytics_owner;
CREATE TABLE analytics.orders (
  id integer PRIMARY KEY,
  total numeric NOT NULL,
  order_date date NOT NULL
);
INSERT INTO analytics.orders (id, total, order_date) VALUES (1, 42.50, DATE '2015-01-01');
RESET ROLE;
GRANT SELECT ON analytics.orders TO analytics;
