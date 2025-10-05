from aws_cdk import (
    Stack,
    Duration,
    aws_lambda as _lambda,
    aws_apigateway as apigw,
    aws_iam as iam,
)
from constructs import Construct
import os

class LineageApiStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs):
        super().__init__(scope, construct_id, **kwargs)

        # Lambda (Python 3.12)
        fn = _lambda.Function(
            self, "LineageApiFn",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="handler.handler",
            code=_lambda.Code.from_asset("lambda"),
            timeout=Duration.seconds(60),
            memory_size=1024,
            environment={
                "DEFAULT_REGION": os.getenv("CDK_DEFAULT_REGION", "ap-northeast-2"),
            },
        )

        # IAM 최소 권한(필요 시 리소스 한정 권고)
        fn.add_to_role_policy(iam.PolicyStatement(
            actions=[
                "sagemaker:ListPipelines",
                "sagemaker:GetPipeline",
                "sagemaker:ListPipelineExecutions",
                "sagemaker:DescribePipelineDefinitionForExecution",
                "sagemaker:ListPipelineExecutionSteps",
                "sagemaker:DescribeTrainingJob",
                "sagemaker:DescribeProcessingJob",
                "sagemaker:ListDomains",
                "sagemaker:ListTags",
            ],
            resources=["*"],
        ))
        # S3 버킷 메타 조회
        fn.add_to_role_policy(iam.PolicyStatement(
            actions=[
                "s3:GetBucketLocation",
                "s3:GetBucketEncryption",
                "s3:GetBucketVersioning",
                "s3:GetPublicAccessBlock",
                "s3:GetBucketTagging",
            ],
            resources=["*"],
        ))
        # (선택) Evaluate 리포트 읽기
        fn.add_to_role_policy(iam.PolicyStatement(
            actions=["s3:GetObject"],
            resources=["arn:aws:s3:::*/*"],  # 실제 운영 시 대상 버킷/prefix로 좁히세요.
        ))

        # API Gateway (REST)
        api = apigw.RestApi(
            self, "LineageApi",
            rest_api_name="SageMaker Lineage API",
            description="Return lineage JSON for SageMaker MLOps pipelines",
            deploy_options=apigw.StageOptions(
                stage_name="prod",
                logging_level=apigw.MethodLoggingLevel.INFO,
                data_trace_enabled=False,
                metrics_enabled=True,
            ),
            default_cors_preflight_options=apigw.CorsOptions(
                allow_origins=apigw.Cors.ALL_ORIGINS,
                allow_methods=["GET", "OPTIONS"],
                allow_headers=apigw.Cors.DEFAULT_HEADERS,
            ),
        )

        lineage_res = api.root.add_resource("lineage")
        lineage_res.add_method(
            "GET",
            apigw.LambdaIntegration(fn, proxy=True),
            request_parameters={
                "method.request.querystring.pipeline": False,
                "method.request.querystring.domain": False,
                "method.request.querystring.includeLatestExec": False,
                "method.request.querystring.region": False,
            },
            # 인증 붙일 경우 authorizer 추가
        )
