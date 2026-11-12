"""기존 operator 자격으로 고정 EKS/ECR 읽기 API만 반복 호출한다."""

from concurrent.futures import ThreadPoolExecutor


class AWSImageReader:
    def __init__(self, *, session=None):
        import boto3
        from botocore.config import Config

        session = session or boto3.Session(profile_name="urlshortener-operator", region_name="ap-northeast-2")
        config = Config(connect_timeout=2, read_timeout=3,
                        retries={"total_max_attempts": 2, "mode": "standard"}, max_pool_connections=4)
        # Session 생성은 직렬, 생성한 clients만 반복·병렬 재사용한다.
        self.sts, self.eks, self.ecr = [session.client(name, region_name="ap-northeast-2", config=config)
                                       for name in ("sts", "eks", "ecr")]

    def _missing(self, digest):
        try:
            self.ecr.describe_images(registryId="764428911194", repositoryName="urlshortener",
                                     imageIds=[{"imageDigest": digest}])
        except self.ecr.exceptions.ImageNotFoundException:
            return True
        return False

    def __call__(self, plan):
        identity = self.sts.get_caller_identity()
        if (identity["Account"] != "764428911194"
                or identity["Arn"] != "arn:aws:iam::764428911194:user/urlshortener-operator"):
            raise ValueError("자동 조회의 기존 operator 계정이 다릅니다")
        normal, failure = plan.normal_image.split("@")[1], plan.failure_image.split("@")[1]
        with ThreadPoolExecutor(max_workers=3) as pool:
            cluster_future = pool.submit(self.eks.describe_cluster, name="urlshortener")
            normal_future = pool.submit(self.ecr.describe_images, registryId="764428911194",
                repositoryName="urlshortener", imageIds=[{"imageDigest": normal}])
            missing_future = pool.submit(self._missing, failure)
            cluster, images, missing = (cluster_future.result()["cluster"],
                normal_future.result()["imageDetails"], missing_future.result())
        if (cluster["arn"] != "arn:aws:eks:ap-northeast-2:764428911194:cluster/urlshortener"
                or cluster["status"] != "ACTIVE" or len(images) != 1
                or images[0]["imageDigest"] != normal or images[0].get("imageStatus") != "ACTIVE"
                or missing is not True):
            raise ValueError("자동 조회의 EKS/ECR 조건이 다릅니다")
        return {"account_id": identity["Account"], "caller_arn": identity["Arn"],
                "cluster_arn": cluster["arn"], "cluster_status": cluster["status"],
                "normal_digest": normal, "normal_status": images[0]["imageStatus"],
                "failure_digest": failure, "failure_absent": missing}
