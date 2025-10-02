#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib';
import { LineageStack } from '../lib/lineage-stack';

const app = new cdk.App();

// 우선순위: env(MARQUEZ_URL) > cdk.json(context.marquezUrl)
const MARQUEZ_URL = process.env.MARQUEZ_URL || app.node.tryGetContext('marquezUrl');
if (!MARQUEZ_URL) throw new Error('MARQUEZ_URL or context.marquezUrl is required');

new LineageStack(app, 'LineageWithCloudTrail', {
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    region: process.env.CDK_DEFAULT_REGION || 'ap-northeast-2',
  },
  marquezUrl: MARQUEZ_URL,
  // VPC 내부 마르퀘즈면 여기에 vpc/subnets/sg 주입하면 됨 (추후 확장)
});
