import pandas as pd
df = pd.read_csv("s3://demo-bucket/raw/orders.jsonl")  # repo_scanner가 S3 URI를 주워감
df.to_parquet("s3://demo-bucket/staging/orders.parquet")
