#!/usr/bin/env python3
import os
import aws_cdk as cdk
from stacks.lineage_api_stack import LineageApiStack

app = cdk.App()

LineageApiStack(
    app, "LineageApiStack",
    env=cdk.Environment(
        account=os.getenv("CDK_DEFAULT_ACCOUNT"),
        region=os.getenv("CDK_DEFAULT_REGION", "ap-northeast-2"),
    ),
)

app.synth()