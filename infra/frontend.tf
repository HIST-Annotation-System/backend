# The public entry point: CloudFront serves the frontend build from a private bucket and passes /api/* to the backend
# host, so the page and the API share one HTTPS origin (*.cloudfront.net). The frontend repository's deploy workflow
# uploads the build and invalidates the cache.

resource "aws_s3_bucket" "frontend" {
  bucket = "${var.bucket_name}-frontend"
  # Only build output, re-created by every deploy.
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "frontend" {
  bucket = aws_s3_bucket.frontend.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_cloudfront_origin_access_control" "frontend" {
  name                              = "wsi-frontend"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

# Only this distribution may read the bucket.
resource "aws_s3_bucket_policy" "frontend" {
  bucket = aws_s3_bucket.frontend.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "cloudfront.amazonaws.com" }
      Action    = "s3:GetObject"
      Resource  = "${aws_s3_bucket.frontend.arn}/*"
      Condition = { StringEquals = { "AWS:SourceArn" = aws_cloudfront_distribution.app.arn } }
    }]
  })
}

# Client-side routes (/slides/<id>, /hotspots, ...) have no file in the bucket: they get index.html. Done here and not
# with custom error pages, which would also replace the API's own 403/404 answers.
resource "aws_cloudfront_function" "spa_routes" {
  name    = "wsi-spa-routes"
  runtime = "cloudfront-js-2.0"
  comment = "Serve index.html for paths without a file extension"
  publish = true
  code    = <<-EOT
    function handler(event) {
      var request = event.request;
      var name = request.uri.split('/').pop();
      if (name.indexOf('.') === -1) {
        request.uri = '/index.html';
      }
      return request;
    }
  EOT
}

data "aws_cloudfront_cache_policy" "caching_optimized" {
  name = "Managed-CachingOptimized"
}

data "aws_cloudfront_cache_policy" "caching_disabled" {
  name = "Managed-CachingDisabled"
}

# Cookies, query strings and all headers except Host (the backend gets its own host name).
data "aws_cloudfront_origin_request_policy" "all_viewer_except_host" {
  name = "Managed-AllViewerExceptHostHeader"
}

resource "aws_cloudfront_distribution" "app" {
  enabled             = true
  is_ipv6_enabled     = true
  comment             = "WSI viewer"
  default_root_object = "index.html"
  # Europe and North America only: the cheapest class, and where the users are.
  price_class = "PriceClass_100"

  origin {
    origin_id                = "frontend"
    domain_name              = aws_s3_bucket.frontend.bucket_regional_domain_name
    origin_access_control_id = aws_cloudfront_origin_access_control.frontend.id
  }

  origin {
    origin_id   = "backend"
    domain_name = aws_eip.backend.public_dns

    custom_origin_config {
      http_port              = 8000
      https_port             = 443
      origin_protocol_policy = "http-only"
      origin_ssl_protocols   = ["TLSv1.2"]
      # The most CloudFront allows without a quota increase; a few requests (first hotspot list of an old folder,
      # large region exports) can take longer than the 30 s default.
      origin_read_timeout = 60
    }
  }

  default_cache_behavior {
    target_origin_id       = "frontend"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    cache_policy_id        = data.aws_cloudfront_cache_policy.caching_optimized.id
    compress               = true

    function_association {
      event_type   = "viewer-request"
      function_arn = aws_cloudfront_function.spa_routes.arn
    }
  }

  # Nothing from the API is cached at the edge: responses depend on the session cookie. The browser still caches
  # tiles and images itself (`Cache-Control: private, immutable` from the API).
  ordered_cache_behavior {
    path_pattern             = "/api/*"
    target_origin_id         = "backend"
    viewer_protocol_policy   = "https-only"
    allowed_methods          = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
    cached_methods           = ["GET", "HEAD"]
    cache_policy_id          = data.aws_cloudfront_cache_policy.caching_disabled.id
    origin_request_policy_id = data.aws_cloudfront_origin_request_policy.all_viewer_except_host.id
  }

  # CloudFront caches error responses for 10 s by default. A slide being copied to the host answers 503 until it is
  # ready, and a restarting API 502: neither may be replayed after the backend has recovered.
  dynamic "custom_error_response" {
    for_each = [502, 503, 504]
    content {
      error_code            = custom_error_response.value
      error_caching_min_ttl = 0
    }
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }
}
