import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as cloudtrail from 'aws-cdk-lib/aws-cloudtrail';
import * as destinations from 'aws-cdk-lib/aws-logs-destinations';

export interface LineageStackProps extends cdk.StackProps {
  marquezUrl: string;
  authHeader?: string;                                // 필요 시
  logRetentionDays?: logs.RetentionDays;              // 기본 1주
}

export class LineageStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: LineageStackProps) {
    super(scope, id, props);

    // 1) CloudTrail 로그를 받을 LogGroup
    const logGroup = new logs.LogGroup(this, 'TrailLogGroup', {
      retention: props.logRetentionDays ?? logs.RetentionDays.ONE_WEEK,
    });

    // 2) CloudTrail (관리 이벤트 + S3/Lambda 데이터 이벤트)
    const trail = new cloudtrail.Trail(this, 'LineageTrail', {
    isMultiRegionTrail: true,
    });
    trail.addCloudWatchLogGroup(logGroup);

    // ✅ S3 데이터 이벤트 (특정 버킷만 지정 권장)
    // *와일드카드(arn:aws:s3:::)는 리전/정책에 따라 거부될 수 있어 버킷 ARN으로 좁히는 게 안전합니다.
    trail.addS3EventSelector(
        [
            'arn:aws:s3:::<YOUR_BUCKET_1>/',     // 예: arn:aws:s3:::my-ingest-bucket/
            'arn:aws:s3:::<YOUR_BUCKET_2>/'      // 필요 없으면 한 개만 넣어도 됨
        ],
        {
            includeManagementEvents: true,
            readWriteType: cloudtrail.ReadWriteType.ALL,
        }
    );

    // ✅ Lambda 데이터 이벤트 (계정 전체 함수 캡처 예시)
    // 리전/계정은 실제 값으로 바꾸세요.
    trail.addLambdaEventSelector(
        [
            'arn:aws:lambda:<REGION>:<ACCOUNT_ID>:function:*' // 예: arn:aws:lambda:ap-northeast-2:123456789012:function:*
        ],
        {
            includeManagementEvents: true,
            readWriteType: cloudtrail.ReadWriteType.ALL,
        }
    );

    // 3) 변환 Lambda
    const fn = new lambda.Function(this, 'LineageEmitter', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'handler.lambda_handler',
      code: lambda.Code.fromAsset('lambda'),
      timeout: cdk.Duration.seconds(30),
      environment: {
        MARQUEZ_URL: props.marquezUrl,
        PRODUCER: 'urn:ai-dspm:cloudtrail:ingestor:1.0',
        OL_NAMESPACE_PREFIX: 'aws://',
        ...(props.authHeader ? { AUTH_HEADER: props.authHeader } : {}),
      },
    });

    // 4) CloudWatch Logs -> Lambda 구독 연결
    new logs.SubscriptionFilter(this, 'TrailToLambda', {
      logGroup,
      destination: new destinations.LambdaDestination(fn),
      filterPattern: logs.FilterPattern.allEvents(),
    });

    // 5) Lambda의 자체 로그 권한
    fn.addToRolePolicy(new iam.PolicyStatement({
      actions: ['logs:CreateLogGroup','logs:CreateLogStream','logs:PutLogEvents'],
      resources: ['*'],
    }));
  }
}
