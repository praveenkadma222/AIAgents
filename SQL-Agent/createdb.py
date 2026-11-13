import sqlite3

#create table 
conn = sqlite3.connect('my_database.db')
cursor = conn.cursor()
#step2:create tables
#tabels:xustomers,order,products,order_details

cursor.execute('''CREATE TABLE IF NOT EXISTS customers (
    customer_id INTEGER PRIMARY KEY AUTOINCREMENT,      
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE, 
    phone TEXT,
    address TEXT  ,
    join_date TEXt  
)''')   

cursor.execute('''CREATE TABLE IF NOT EXISTS products (
    product_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT,   
    price REAL NOT NULL,
    stock_quantity INTEGER NOT NULL,
    category TEXT
)''')

cursor.execute('''CREATE TABLE IF NOT EXISTS orders (
    order_id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL,
    order_date TEXT NOT NULL,
    total_amount REAL NOT NULL,

    FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
)''')

cursor.execute('''CREATE TABLE IF NOT EXISTS order_details (
    order_detail_id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL,
    product_id INTEGER NOT NULL,
    quantity INTEGER NOT NULL,
    price REAL NOT NULL,
subtotal REAL NOT NULL,
    FOREIGN KEY (order_id) REFERENCES orders(order_id),
    FOREIGN KEY (product_id) REFERENCES products(product_id)
)''')

#stpe3:Enter dummy data into the tables

#customers
# 1. Insert Dummy Customers
customers_data = [
    ("Alice Smith", "alice.smith@email.com", "555-0101", "123 Maple St, Seattle", "2026-01-15"),
    ("Bob Jones", "bob.jones@email.com", "555-0102", "456 Oak Road, Austin", "2026-02-20"),
    ("Charlie Brown", "charlie.b@email.com", "555-0103", "789 Pine Ave, New York", "2026-03-05")
]

cursor.executemany('''
    INSERT INTO customers (name, email, phone, address, join_date)
    VALUES (?, ?, ?, ?, ?)
''', customers_data)


# 2. Insert Dummy Products
products_data = [
    ("Wireless Mouse", "Ergonomic 2.4GHz wireless mouse", 29.99, 150, "Electronics"),
    ("Mechanical Keyboard", "RGB backlit mechanical keyboard", 89.99, 45, "Electronics"),
    ("Water Bottle", "1L stainless steel insulated bottle", 24.99, 200, "Kitchenware"),
    ("Desk Mat", "Large felt desk organizer mat", 19.99, 80, "Office Supplies")
]

cursor.executemany('''
    INSERT INTO products (name, description, price, stock_quantity, category)
    VALUES (?, ?, ?, ?, ?)
''', products_data)


# 3. Insert Dummy Orders
# Match up with customer_id (1 = Alice, 2 = Bob)
orders_data = [
    (1, "2026-10-25", 149.97),  # Alice bought 2 Keyboards & 1 Mouse
    (1, "2026-10-28", 24.99),   # Alice bought 1 Water Bottle
    (2, "2026-10-30", 49.98)    # Bob bought 1 Mouse & 1 Desk Mat
]

cursor.executemany('''
    INSERT INTO orders (customer_id, order_date, total_amount)
    VALUES (?, ?, ?)
''', orders_data)


# 4. Insert Dummy Order Details
# Match up with order_id and product_id 
order_details_data = [
    # Order 1 details (Alice's first order)
    (1, 1, 1, 29.99, 29.99),    # 1x Wireless Mouse
    (1, 2, 2, 89.99, 119.98),   # 2x Mechanical Keyboard
    
    # Order 2 details (Alice's second order)
    (2, 3, 1, 24.99, 24.99),    # 1x Water Bottle
    
    # Order 3 details (Bob's order)
    (3, 1, 1, 29.99, 29.99),    # 1x Wireless Mouse
    (3, 4, 1, 19.99, 19.99)     # 1x Desk Mat
]

cursor.executemany('''
    INSERT INTO order_details (order_id, product_id, quantity, price, subtotal)
    VALUES (?, ?, ?, ?, ?)
''', order_details_data)

# Don't forget to commit the changes to your database!
# connection.commit()
conn.commit()
conn.close()
print("Database and tables created successfully with dummy data.")