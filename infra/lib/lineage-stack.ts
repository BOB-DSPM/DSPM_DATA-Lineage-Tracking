import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as cloudtrail from 'aws-cdk-lib/aws-cloudtrail';
import * as destinations from 'aws-cdk-lib/aws-logs-destinations';

export interface LineageStackProps extends cdk.StackProps {
  marquezUrl: string;
  authHeader?: string;
  logRetentionDays?: logs.RetentionDays;
}

export class LineageStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: LineageStackProps) {
    super(scope, id, props);

    // 1) CloudTrail 로그 수신용 LogGroup
    const logGroup = new logs.LogGroup(this, 'TrailLogGroup', {
      retention: props.logRetentionDays ?? logs.RetentionDays.ONE_WEEK,
    });

    // 2) CloudTrail 생성 (버전 무관 기본형)
    const trail = new cloudtrail.Trail(this, 'LineageTrail', {
      isMultiRegionTrail: true, // 이벤트 과다 시 false로
    });

    // 2-1) L1(CfnTrail) 핸들 얻기
    const cfnTrail = trail.node.defaultChild as cloudtrail.CfnTrail;

    // 2-2) Trail → CloudWatch Logs 연결 (CFN 속성으로 지정: 버전 무관)
    // CloudTrail이 로그를 쓸 수 있는 역할
    const trailLogsRole = new iam.Role(this, 'TrailLogsRole', {
      assumedBy: new iam.ServicePrincipal('cloudtrail.amazonaws.com'),
    });
    // LogGroup에 쓰기 권한 부여
    logGroup.grantWrite(trailLogsRole);

    // CFN 속성 연결
    cfnTrail.addPropertyOverride('CloudWatchLogsLogGroupArn', logGroup.logGroupArn);
    cfnTrail.addPropertyOverride('CloudWatchLogsRoleArn', trailLogsRole.roleArn);

    // 2-3) Advanced Event Selectors 주입 (CFN 원형 키 사용: Name/FieldSelectors/Field/Equals)
    cfnTrail.addPropertyOverride('AdvancedEventSelectors', [
      {
        Name: 'AllS3ObjectDataEvents',
        FieldSelectors: [
          { Field: 'eventCategory',   Equals: ['Data'] },
          { Field: 'resources.type',  Equals: ['AWS::S3::Object'] }
        ]
      },
      {
        Name: 'AllLambdaFunctionDataEvents',
        FieldSelectors: [
          { Field: 'eventCategory',   Equals: ['Data'] },
          { Field: 'resources.type',  Equals: ['AWS::Lambda::Function'] }
        ]
      }
    ]);

    // 3) 변환 Lambda (CloudWatch Logs 구독 이벤트 → OpenLineage POST)
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

    // 4) CloudWatch Logs → Lambda 구독
    new logs.SubscriptionFilter(this, 'TrailToLambda', {
      logGroup,
      destination: new destinations.LambdaDestination(fn),
      filterPattern: logs.FilterPattern.allEvents(),
    });

    // 5) Lambda 자체 로그 권한
    fn.addToRolePolicy(new iam.PolicyStatement({
      actions: ['logs:CreateLogGroup', 'logs:CreateLogStream', 'logs:PutLogEvents'],
      resources: ['*'],
    }));
  }
}
